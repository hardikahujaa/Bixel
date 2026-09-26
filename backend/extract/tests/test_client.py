"""Tests for the LLM client adapter.

No network in the default suite. What is tested here is the wiring that matters when the
network misbehaves: that the model chain is walked in order, that a dead chain raises
``LLMUnavailable`` rather than something the pipeline would not catch, and that the fake
client behaves like the real one closely enough to be worth testing against.

The real client's behaviour against live Gemini is covered in test_live.py, which is
skipped unless BIXEL_LIVE_LLM=1.
"""

from __future__ import annotations

import pytest

from backend.extract.client import (
    ATTEMPTS_PER_MODEL,
    MODEL_CHAIN,
    FakeClient,
    GeminiClient,
    LLMClient,
    LLMUnavailable,
)


# ----------------------------------------------------------------- the fake -----


def test_fake_returns_scripted_responses_in_order():
    client = FakeClient(['{"a": 1}', '{"b": 2}'])
    assert client.generate_json("p1") == '{"a": 1}'
    assert client.generate_json("p2") == '{"b": 2}'
    assert client.prompts == ["p1", "p2"]
    assert client.call_count == 2


def test_fake_accepts_a_bare_string():
    assert FakeClient('{"a": 1}').generate_json("p") == '{"a": 1}'


def test_fake_raises_scripted_exceptions():
    """This is how the pipeline's failure paths get exercised offline."""
    client = FakeClient([LLMUnavailable("down"), '{"ok": true}'])
    with pytest.raises(LLMUnavailable):
        client.generate_json("p")
    assert client.generate_json("p") == '{"ok": true}'


def test_fake_running_dry_raises_rather_than_returning_nonsense():
    client = FakeClient([])
    with pytest.raises(LLMUnavailable):
        client.generate_json("p")


def test_fake_satisfies_the_protocol():
    assert isinstance(FakeClient("{}"), LLMClient)


# ------------------------------------------------------------ the real client -----


def test_real_client_satisfies_the_protocol_without_being_constructed():
    """Structural check only -- constructing it needs a key, calling it needs network."""
    assert hasattr(GeminiClient, "generate_json")


def test_missing_key_raises_llm_unavailable(monkeypatch):
    """The pipeline must be able to catch this and fall back, not crash the request."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("backend.extract.client.load_api_key", lambda: None)
    with pytest.raises(LLMUnavailable):
        GeminiClient()


def test_dead_chain_raises_llm_unavailable_and_names_every_attempt(monkeypatch):
    """Measured reality: three of six probed models returned 503. A dead chain is not
    hypothetical, so it must fail in a way the pipeline can act on."""

    class Boom:
        class models:
            @staticmethod
            def generate_content(**_kwargs):
                raise RuntimeError("503 UNAVAILABLE")

    client = GeminiClient(api_key="test-key", models=("m1", "m2"), attempts_per_model=2)
    monkeypatch.setattr(client, "_lazy_client", lambda: Boom)
    monkeypatch.setattr(client, "_config", lambda: None)

    with pytest.raises(LLMUnavailable) as excinfo:
        client.generate_json("prompt")

    message = str(excinfo.value)
    assert "m1 attempt 1" in message and "m1 attempt 2" in message
    assert "m2 attempt 1" in message and "m2 attempt 2" in message
    assert len(client.failures) == 4


def test_chain_moves_to_the_next_model_after_failures(monkeypatch):
    calls: list[str] = []

    class Flaky:
        class models:
            @staticmethod
            def generate_content(model, **_kwargs):
                calls.append(model)
                if model == "first":
                    raise RuntimeError("503")

                class Response:
                    text = '{"ok": true}'

                return Response

    client = GeminiClient(api_key="k", models=("first", "second"), attempts_per_model=2)
    monkeypatch.setattr(client, "_lazy_client", lambda: Flaky)
    monkeypatch.setattr(client, "_config", lambda: None)

    assert client.generate_json("p") == '{"ok": true}'
    assert calls == ["first", "first", "second"], calls
    assert client.last_model == "second"


def test_empty_model_body_is_treated_as_a_failure(monkeypatch):
    """A 200 with an empty body is still unusable, and must not be returned as valid."""

    class Blank:
        class models:
            @staticmethod
            def generate_content(**_kwargs):
                class Response:
                    text = "   "

                return Response

    client = GeminiClient(api_key="k", models=("only",), attempts_per_model=1)
    monkeypatch.setattr(client, "_lazy_client", lambda: Blank)
    monkeypatch.setattr(client, "_config", lambda: None)
    with pytest.raises(LLMUnavailable):
        client.generate_json("p")


# ----------------------------------------------------------------- the config -----


def test_model_chain_is_ordered_fastest_first():
    """Ordering is from the measured probe recorded in client.py, not from preference."""
    assert MODEL_CHAIN[0] == "gemini-3.5-flash-lite"
    assert len(MODEL_CHAIN) >= 2, "a single model is not a chain -- 503s were measured"
    assert ATTEMPTS_PER_MODEL >= 2


def test_unavailable_models_are_not_in_the_chain():
    """gemini-3.7-flash never answered in the probe; gemini-3.5-flash took 26s once."""
    assert "gemini-3.7-flash" not in MODEL_CHAIN
    assert "gemini-3.5-flash" not in MODEL_CHAIN
