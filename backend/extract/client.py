"""LLM client for the extraction pipeline.

Thin adapter over one capability -- "send a prompt, get JSON text back" -- so the pipeline
can be tested without a network call, a key or a quota. ``extract()`` takes a client by
keyword, defaulting to the real one.

MODEL CHOICE IS MEASURED, NOT ASSUMED. Probing the account's own model list with two
attempts each (26 Sept 2026):

    gemini-3.5-flash-lite    1.28s   1.18s   <- consistently fast and available
    gemini-3.1-flash-lite    1.21s   3.10s
    gemini-3.8-flash         2.29s   2.13s
    gemini-3.6-flash          503    2.54s
    gemini-3.5-flash          503    26.62s  <- 26 seconds when it did answer
    gemini-3.7-flash          503     503    <- never answered

The larger flash models are returning 503 "high demand". That is why this module has a
model fallback chain rather than a single model name, and it is a large part of why the
pipeline needs a deterministic no-LLM fallback behind it: on the judging day the model we
want may simply not answer.

flash-lite is the primary on merit -- it was both the fastest and the only one to return a
byte-exact ``goal`` string on the first structured-output probe.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

#: Tried in order. First one that answers wins.
MODEL_CHAIN: tuple[str, ...] = (
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
)

#: Per-attempt wall-clock ceiling. A3 budgets 8s for cold start, and the deterministic
#: fallback is better than a request that hangs.
REQUEST_TIMEOUT_SECONDS = 20.0

#: Retries per model before moving to the next one. 503 was transient in the probe above,
#: so one retry is worth it; more than that just delays the fallback.
ATTEMPTS_PER_MODEL = 2

REPO_ROOT = Path(__file__).resolve().parents[2]


class LLMUnavailable(RuntimeError):
    """Every model in the chain failed. The caller must fall back, not propagate."""


@runtime_checkable
class LLMClient(Protocol):
    """The whole contract. Keeping it this small is what makes the fakes honest.

    ``runtime_checkable`` so the pipeline can reject a bad injected client with a clear
    message instead of an AttributeError deep in a request."""

    def generate_json(self, prompt: str) -> str:
        """Return the model's raw response text, expected to be JSON."""
        ...


def load_api_key() -> str | None:
    """Read GEMINI_API_KEY from the environment or .env. Never logged."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:  # pragma: no cover - python-dotenv is a declared dependency
        return None
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


class GeminiClient:
    """Real Gemini client, using google-genai's JSON response mode.

    ``response_mime_type="application/json"`` matters: it makes the model return a bare
    JSON document rather than a fenced code block, which removes a whole class of parsing
    failure. Automatic function calling is disabled because we pass no tools and the SDK
    otherwise logs a warning on every call.
    """

    def __init__(
        self,
        api_key: str | None = None,
        models: Sequence[str] = MODEL_CHAIN,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        attempts_per_model: int = ATTEMPTS_PER_MODEL,
    ) -> None:
        self.api_key = api_key or load_api_key()
        if not self.api_key:
            raise LLMUnavailable(
                "no GEMINI_API_KEY in the environment or .env -- set one, or pass a client"
            )
        self.models = tuple(models)
        self.timeout = timeout
        self.attempts_per_model = attempts_per_model
        self.last_model: str | None = None
        self.failures: list[str] = []
        self._client = None

    def _lazy_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _config(self):
        from google.genai import types

        return types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,  # extraction, not creative writing -- determinism helps caching
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=types.HttpOptions(timeout=int(self.timeout * 1000)),
        )

    def generate_json(self, prompt: str) -> str:
        client = self._lazy_client()
        config = self._config()
        self.failures = []

        for model in self.models:
            for attempt in range(1, self.attempts_per_model + 1):
                try:
                    started = time.perf_counter()
                    response = client.models.generate_content(
                        model=model, contents=prompt, config=config
                    )
                    text = (response.text or "").strip()
                    if not text:
                        raise ValueError("model returned an empty body")
                    self.last_model = model
                    self.last_latency = time.perf_counter() - started
                    return text
                except Exception as exc:  # noqa: BLE001 - any failure moves us along the chain
                    self.failures.append(f"{model} attempt {attempt}: {type(exc).__name__}")
        raise LLMUnavailable(
            "every model in the chain failed: " + "; ".join(self.failures)
        )


class FakeClient:
    """Scripted client for tests. Ships here rather than in the test folder so M3 can use
    it for API-level tests without depending on our test layout.

    Give it a list of responses; each call pops the next one. A response that is an
    exception instance is raised instead of returned, which is how the failure paths get
    exercised without touching the network.
    """

    def __init__(self, responses: Sequence[str | BaseException] | str | BaseException):
        if isinstance(responses, (str, BaseException)):
            responses = [responses]
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.last_model = "fake"

    def generate_json(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise LLMUnavailable("FakeClient ran out of scripted responses")
        nxt = self._responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    @property
    def call_count(self) -> int:
        return len(self.prompts)
