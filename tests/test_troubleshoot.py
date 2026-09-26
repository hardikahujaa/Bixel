"""Rigorous tests for POST /v1/troubleshoot against the actual grading
contract (docs/KIT_NOTES.md section 8), not just "it returns 200". These pass
against today's hardcoded scaffold placeholder, and they're written to keep
passing once the real extract() -> to_deeplink_pair() -> sanitize() ->
validate() pipeline replaces it -- they encode the formatting rules
directly, which is also most of what M2's validate() has to check.
"""
import json
import re

from fastapi.testclient import TestClient

from app.main import app
from student_kit.schema import ContextDeeplinkResponse

client = TestClient(app)

VALID_PAYLOAD = {
    "query": "My screen is black",
    "siis_response": {"title": "Blank screen", "content": "Some content"},
}

GOAL_REGEX = re.compile(r"^Follow these steps to perform this .+ (Troubleshooting|Configuration)\.$")
BANNED_URL_SUBSTRINGS = ["http://", "https://", "www.", ".com", ".html", "![", "](http"]


def _post(payload: dict):
    return client.post("/v1/troubleshoot", json=payload)


def test_returns_200_and_validates_against_the_graders_schema():
    response = _post(VALID_PAYLOAD)
    assert response.status_code == 200
    # Must hold against student_kit/schema.py itself, not just "some JSON".
    ContextDeeplinkResponse.model_validate(response.json())


def test_never_returns_empty_contexts():
    """Banned by decision (docs/KIT_NOTES.md section 1): {"contexts": []} passes
    G4 and G5 but scores zero on generalization and signals a broken
    pipeline -- every path, including today's placeholder, must avoid it."""
    assert _post(VALID_PAYLOAD).json()["contexts"] != []


def test_missing_query_is_rejected_with_422():
    payload = {"siis_response": VALID_PAYLOAD["siis_response"]}
    assert _post(payload).status_code == 422


def test_missing_siis_response_is_rejected_with_422():
    payload = {"query": VALID_PAYLOAD["query"]}
    assert _post(payload).status_code == 422


def test_siis_response_missing_content_is_rejected_with_422():
    payload = {"query": "x", "siis_response": {"title": "t"}}
    assert _post(payload).status_code == 422


def test_empty_body_is_rejected_with_422():
    assert _post({}).status_code == 422


def test_wrong_type_for_query_is_rejected_with_422():
    payload = {"query": 12345, "siis_response": VALID_PAYLOAD["siis_response"]}
    assert _post(payload).status_code == 422


def test_response_contains_no_url_shaped_text():
    """Gate G5: zero URL leaks anywhere in the output. The one gate a
    single leaked 'samsung.com' zeroes the entire automated score over."""
    body_text = json.dumps(_post(VALID_PAYLOAD).json()).lower()
    for banned in BANNED_URL_SUBSTRINGS:
        assert banned not in body_text, f"found banned substring {banned!r} in response"


def test_every_goal_matches_the_required_regex_with_trailing_period():
    for ctx in _post(VALID_PAYLOAD).json()["contexts"]:
        assert GOAL_REGEX.match(ctx["goal"]), f"goal {ctx['goal']!r} does not match the required pattern"


def test_every_title_is_two_to_three_words():
    for ctx in _post(VALID_PAYLOAD).json()["contexts"]:
        word_count = len(ctx["title"].split())
        assert 2 <= word_count <= 3, f"title {ctx['title']!r} has {word_count} words"


def test_every_score_is_in_range():
    for ctx in _post(VALID_PAYLOAD).json()["contexts"]:
        assert 0.0 <= ctx["score"] <= 1.0


def test_every_action_description_is_five_to_seven_words_starting_with_it_will():
    for ctx in _post(VALID_PAYLOAD).json()["contexts"]:
        for action in ctx["actions"]:
            words = action["description"].split()
            assert 5 <= len(words) <= 7, f"{action['description']!r} has {len(words)} words"
            assert action["description"].startswith("It will"), action["description"]


def test_every_step_group_has_non_empty_steps():
    for ctx in _post(VALID_PAYLOAD).json()["contexts"]:
        for action in ctx["actions"]:
            for step_group in action["stepGroups"]:
                assert step_group["steps"], "a stepGroup has an empty steps list"


def test_every_action_has_an_explicit_category():
    """schema.py lets category default to 'manual' silently (docs/KIT_NOTES.md
    section 1) -- assert it's actually present in the payload we send out,
    not relying on the default to paper over a dropped field."""
    for ctx in _post(VALID_PAYLOAD).json()["contexts"]:
        for action in ctx["actions"]:
            assert action["category"] in {"auto", "manual", "critical"}


def test_every_auto_action_has_an_actionable_deeplink():
    for ctx in _post(VALID_PAYLOAD).json()["contexts"]:
        for action in ctx["actions"]:
            if action["category"] == "auto":
                for step_group in action["stepGroups"]:
                    assert step_group["actionableDeeplink"] is not None, "auto action missing actionableDeeplink"


def test_response_is_deterministic_for_the_same_request():
    first = _post(VALID_PAYLOAD).json()
    second = _post(VALID_PAYLOAD).json()
    assert first == second


def test_a_fallback_route_is_never_cached():
    """A request whose LLM call fails takes extract()'s deterministic
    fallback (still a real, valid, non-empty answer) -- but app/main.py must
    not cache it, or a transient Gemini failure (docs/PLAN.md's named risk)
    would serve that degraded answer to every later paraphrase forever
    instead of retrying. Two identical requests during an outage should both
    be misses, and both should still pass every rule."""
    from app.main import app, get_llm_client, _cache
    from backend.extract.client import FakeClient, LLMUnavailable

    app.dependency_overrides[get_llm_client] = lambda: FakeClient(
        [LLMUnavailable("simulated outage"), LLMUnavailable("simulated outage")]
    )
    try:
        first = _post(VALID_PAYLOAD)
        second = _post(VALID_PAYLOAD)
    finally:
        app.dependency_overrides.pop(get_llm_client, None)

    assert first.status_code == second.status_code == 200
    assert first.json()["contexts"] and second.json()["contexts"]
    assert _cache.hits == 0
    assert _cache.misses == 2
