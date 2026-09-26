"""Tests for extract(). Offline and deterministic -- every LLM response is injected.

The interesting tests here are not the happy path. They are the ones proving the guarantees
hold when the model misbehaves: that an invented URI cannot reach the output, that a step
the document does not support gets dropped, that the samsung.com rows never leak, and that
``contexts`` is never empty no matter what comes back.

Live behaviour against real Gemini is in test_live.py.
"""

from __future__ import annotations

import json

import pytest

from app.validator import validate
from backend.extract.client import FakeClient, LLMUnavailable
from backend.extract.pipeline import ExtractionTrace, extract
from backend.matcher.catalog import contains_url


@pytest.fixture(scope="module")
def kit():
    payload = json.loads(open("student_kit/siis_responses.json", encoding="utf-8").read())
    return {row["id"]: row for row in payload["responses"]}


def no_candidates(text, heading=None):
    """Matcher stub. Keeps these tests off the embedding model entirely."""
    return []


def answer(**overrides):
    """A well-formed model answer, grounded in row_21's touch-sensitivity text."""
    action = {
        "actionName": "Disable Touch Sensitivity",
        "description": "It will lower screen touch sensitivity",
        "category": "manual",
        "section": 1,
        "shortcut": None,
        "steps": [
            "To turn off this feature, navigate to Settings, tap Display, and then tap "
            "the switch next to Touch sensitivity."
        ],
    }
    action.update(overrides.pop("action", {}))
    goal = {
        "name": "Touch Sensitivity",
        "title": "Touch sensitivity fix",
        "score": 0.9,
        "actions": [action],
    }
    goal.update(overrides.pop("goal", {}))
    return json.dumps({"goals": [goal]})


def run(kit, row_id, responses, **kwargs):
    row = kit[row_id]
    trace = ExtractionTrace()
    result = extract(
        row["original_query"],
        row["siis_response"],
        client=FakeClient(responses),
        matcher=kwargs.pop("matcher", no_candidates),
        trace=trace,
    )
    return result.model_dump(mode="json"), trace


# ------------------------------------------------------------------ happy path -----


def test_well_formed_answer_takes_the_model_route(kit):
    payload, trace = run(kit, "row_21", answer())
    assert trace.route == "model"
    assert trace.attempts == 1
    assert validate(payload)["ok"] is True


def test_goal_sentence_is_built_by_us_not_by_the_model(kit):
    """The model supplies a name; we build the regex-critical wrapper and its period.

    That way the one string A1 checks with a regex is never at the mercy of the model's
    formatting.
    """
    payload, _ = run(kit, "row_21", answer())
    goal = payload["contexts"][0]["goal"]
    assert goal == "Follow these steps to perform this Touch Sensitivity Troubleshooting."


def test_a_model_supplied_goal_sentence_does_not_double_up(kit):
    """If the model ignores instructions and returns a whole sentence or adds the word
    "Troubleshooting" itself, we must not produce "... Troubleshooting Troubleshooting."."""
    payload, _ = run(kit, "row_21", answer(goal={"name": "Touch Sensitivity Troubleshooting"}))
    goal = payload["contexts"][0]["goal"]
    assert goal.count("Troubleshooting") == 1, goal
    assert validate(payload)["ok"] is True


@pytest.mark.parametrize(
    "bad_description",
    [
        "It will do a great many different things here",  # too long
        "It will help",                                    # too short
        "Lowers the screen touch sensitivity now",          # wrong prefix
        "",
        None,
        123,
    ],
)
def test_a_bad_description_is_replaced_not_passed_through(kit, bad_description):
    payload, _ = run(kit, "row_21", answer(action={"description": bad_description}))
    description = payload["contexts"][0]["actions"][0]["description"]
    assert description.startswith("It will")
    assert 5 <= len(description.split()) <= 7
    assert validate(payload)["ok"] is True


@pytest.mark.parametrize("bad_title", ["One", "Four whole words here now", "", None])
def test_a_bad_title_is_replaced(kit, bad_title):
    payload, _ = run(kit, "row_21", answer(goal={"title": bad_title}))
    assert 2 <= len(payload["contexts"][0]["title"].split()) <= 3
    assert validate(payload)["ok"] is True


@pytest.mark.parametrize("bad_score,expected", [(5.0, 1.0), (-2.0, 0.0), ("high", 0.5), (None, 0.5)])
def test_score_is_clamped_into_range(kit, bad_score, expected):
    payload, _ = run(kit, "row_21", answer(goal={"score": bad_score}))
    assert payload["contexts"][0]["score"] == expected


# ------------------------------------------------- the deeplink guarantee -----


def test_an_invented_uri_never_reaches_the_output(kit):
    """The core guarantee. The model is never shown a URI and never asked for one, and
    nothing it writes is copied into a deeplink field -- so even when it invents one, the
    string cannot survive.
    """
    payload, _ = run(
        kit,
        "row_21",
        answer(
            action={
                "steps": [
                    "To turn off this feature, navigate to Settings, tap Display, and "
                    "then tap the switch next to Touch sensitivity.",
                    "Open bixby://masked/act/deadbeef00 to finish.",
                ]
            }
        ),
    )
    serialised = json.dumps(payload)
    assert "deadbeef00" not in serialised
    assert "bixby://" not in serialised, "no deeplink was resolved, so none should appear"


def test_an_out_of_range_shortcut_number_becomes_no_deeplink(kit):
    """An index the candidate list never offered must resolve to null, not to some entry."""
    payload, _ = run(kit, "row_21", answer(action={"shortcut": 99, "category": "auto"}))
    group = payload["contexts"][0]["actions"][0]["stepGroups"][0]
    assert group["actionableDeeplink"] is None
    assert payload["contexts"][0]["actions"][0]["category"] == "manual"
    assert validate(payload)["ok"] is True


def test_auto_is_downgraded_when_no_shortcut_resolved(kit):
    """An "auto" action with no deeplink is an automatic A2 deduction, so it is corrected
    before the validator ever sees it."""
    payload, _ = run(kit, "row_21", answer(action={"category": "auto", "shortcut": None}))
    assert payload["contexts"][0]["actions"][0]["category"] == "manual"


@pytest.mark.parametrize("junk", ["two", None, -1, 0, 1.7, [], {}])
def test_junk_shortcut_values_do_not_crash(kit, junk):
    payload, _ = run(kit, "row_21", answer(action={"shortcut": junk}))
    assert validate(payload)["ok"] is True


def test_a_real_candidate_resolves_to_a_verbatim_catalog_deeplink(kit):
    """With a candidate offered and chosen, the URI must come from the catalog untouched."""
    from backend.matcher.catalog import load_catalog

    entry = {e.id: e.raw for e in load_catalog()}["DL-0125"]

    class Candidate:
        catalog_id = "DL-0125"
        score = 0.9
        polarity = -1

    Candidate.entry = entry

    def one_candidate(text, heading=None):
        return [Candidate]

    payload, _ = run(
        kit, "row_21", answer(action={"shortcut": 1, "category": "auto"}), matcher=one_candidate
    )
    group = payload["contexts"][0]["actions"][0]["stepGroups"][0]
    assert group["actionableDeeplink"] is not None
    assert group["actionableDeeplink"]["deeplink"] == entry["deeplink"]
    assert group["actionableDeeplink"]["description"] == entry["description"]
    assert payload["contexts"][0]["actions"][0]["category"] == "auto"
    assert validate(payload)["ok"] is True


# ----------------------------------------------------------------- grounding -----


def test_an_ungrounded_step_is_dropped(kit):
    """A plausible instruction the document does not contain must not survive."""
    payload, trace = run(
        kit,
        "row_21",
        answer(
            action={
                "steps": [
                    "To turn off this feature, navigate to Settings, tap Display, and "
                    "then tap the switch next to Touch sensitivity.",
                    "Open Settings and tap Battery and device care to optimise power.",
                ]
            }
        ),
    )
    steps = payload["contexts"][0]["actions"][0]["stepGroups"][0]["steps"]
    assert len(steps) == 1
    assert "Battery and device care" not in json.dumps(payload)
    assert trace.grounding and "dropped 1" in trace.grounding


def test_a_fully_invented_answer_falls_back_rather_than_shipping(kit):
    """If nothing is grounded, the model answered from memory. Fall back to the document."""
    invented = json.dumps(
        {
            "goals": [
                {
                    "name": "Battery",
                    "title": "Battery drain fix",
                    "score": 0.95,
                    "actions": [
                        {
                            "actionName": "Optimise Battery",
                            "description": "It will optimise the battery usage",
                            "category": "manual",
                            "section": 1,
                            "shortcut": None,
                            "steps": ["Open Settings and tap Battery and device care."],
                        }
                    ],
                }
            ]
        }
    )
    payload, trace = run(kit, "row_21", [invented, invented])
    assert trace.route.startswith("fallback")
    assert payload["contexts"], "must never be empty"
    assert validate(payload)["ok"] is True


# ------------------------------------------------------------- failure paths -----


@pytest.mark.parametrize(
    "responses,expected_route",
    [
        (["not json", "still not json"], "fallback:invalid-after-retry"),
        (["[]", "[]"], "fallback:invalid-after-retry"),
        (['{"goals": []}', '{"goals": []}'], "fallback:ungrounded"),
        ([LLMUnavailable("all models 503")], "fallback:llm-error"),
        ([RuntimeError("boom")], "fallback:llm-error"),
        ([TimeoutError("timed out")], "fallback:llm-error"),
    ],
)
def test_every_failure_mode_falls_back_to_a_valid_non_empty_response(
    kit, responses, expected_route
):
    payload, trace = run(kit, "row_21", responses)
    assert trace.route == expected_route
    assert payload["contexts"], "contexts is never empty -- banned project-wide"
    assert validate(payload)["ok"] is True


def test_a_repairable_formatting_error_does_not_cost_a_retry(kit):
    """Worth pinning, because it is a deliberate latency choice.

    A bad title, description or score is repaired locally in ``_assemble`` rather than sent
    back to the model. A retry costs another 1-3 seconds of real latency, and we can fix
    these deterministically, so the model is only asked again when we genuinely cannot
    recover -- which in practice means unparseable JSON.
    """
    client = FakeClient([answer(goal={"title": "One", "score": 5.0})])
    row = kit["row_21"]
    trace = ExtractionTrace()
    result = extract(
        row["original_query"],
        row["siis_response"],
        client=client,
        matcher=no_candidates,
        trace=trace,
    )
    assert client.call_count == 1, "repairable errors should not trigger a second call"
    assert trace.route == "model"
    payload = result.model_dump(mode="json")
    assert 2 <= len(payload["contexts"][0]["title"].split()) <= 3
    assert payload["contexts"][0]["score"] == 1.0
    assert validate(payload)["ok"] is True


def test_the_retry_feeds_the_rejection_reason_back(kit):
    """When a retry does happen, the prompt says why the last answer was rejected.

    For unparseable JSON that is our own message; for a validation failure it is
    ``validate()``'s own error strings, which already name the exact rule and path.
    """
    client = FakeClient(["this is not json at all", answer()])
    row = kit["row_21"]
    trace = ExtractionTrace()
    extract(
        row["original_query"],
        row["siis_response"],
        client=client,
        matcher=no_candidates,
        trace=trace,
    )
    assert client.call_count == 2
    assert "REJECTED" in client.prompts[1]
    assert "JSON" in client.prompts[1]
    assert trace.route == "model", "the retry should have succeeded"


def test_only_one_retry_happens(kit):
    client = FakeClient(["bad", "bad", "bad", "bad"])
    row = kit["row_21"]
    extract(row["original_query"], row["siis_response"], client=client, matcher=no_candidates)
    assert client.call_count == 2, "a second retry costs latency for a model already failing"


# ----------------------------------------------------------- malformed input -----


@pytest.mark.parametrize(
    "query,siis",
    [
        ("screen black", {"title": "t", "content": ""}),
        ("screen black", {}),
        ("screen black", None),
        ("", {"title": "", "content": ""}),
        (None, None),
        ("screen black", {"title": None, "content": None}),
        ("screen black", "not a dict"),
        ("screen black", {"content": "prose with no headings at all."}),
    ],
)
def test_malformed_input_never_crashes_and_never_returns_empty(query, siis):
    trace = ExtractionTrace()
    result = extract(query, siis, client=FakeClient(answer()), matcher=no_candidates, trace=trace)
    payload = result.model_dump(mode="json")
    assert payload["contexts"], f"empty contexts for {siis!r}"
    assert validate(payload)["ok"] is True


# ------------------------------------------------------------- the URL rows -----


@pytest.mark.parametrize("row_id", ["row_3", "row_11", "row_17"])
def test_the_samsung_com_rows_never_leak_a_url(kit, row_id):
    """These three contain kidshome.pin@samsung.com. G5 is a hard gate: one leak zeroes the
    entire automated score. Tested on both the model route and the fallback route."""
    for responses in (answer(), [LLMUnavailable("down")]):
        payload, _ = run(kit, row_id, responses)
        serialised = json.dumps(payload)
        assert "samsung.com" not in serialised
        assert not contains_url(serialised.replace("bixby://", ""))
        assert validate(payload)["ok"] is True


def test_a_model_echoing_the_leak_has_it_stripped(kit):
    """Even if the model copies the email out of the document, it must not survive."""
    payload, _ = run(
        kit,
        "row_3",
        answer(
            action={
                "steps": [
                    "Send a blank email to kidshome.pin@samsung.com using your "
                    "registered email address."
                ]
            }
        ),
    )
    assert "samsung.com" not in json.dumps(payload)
    assert validate(payload)["ok"] is True


# --------------------------------------------------------- the whole kit, offline -----


def test_every_kit_row_produces_a_valid_response_on_the_fallback_route(kit):
    """20 for 20 with the LLM unavailable. This is the floor the service can always hit."""
    failures = []
    for row_id in kit:
        payload, trace = run(kit, row_id, [LLMUnavailable("down")])
        result = validate(payload)
        if not (result["ok"] and payload["contexts"]):
            failures.append((row_id, result["errors"][:2]))
    assert failures == []


def test_every_kit_row_produces_a_valid_response_on_the_model_route(kit):
    """The same 20 with a grounded answer injected, so the assembly path is exercised on
    every document shape in the kit rather than just one."""
    failures = []
    for row_id, row in kit.items():
        content = row["siis_response"]["content"]
        first_line = next(
            (l.strip() for l in content.split("\n") if l.strip() and not l.startswith("#")),
            "",
        )
        if not first_line:
            continue
        injected = json.dumps(
            {
                "goals": [
                    {
                        "name": "Device Issue",
                        "title": "Device issue check",
                        "score": 0.7,
                        "actions": [
                            {
                                "actionName": "Follow Guidance",
                                "description": "It will help you follow guidance",
                                "category": "manual",
                                "section": 1,
                                "shortcut": None,
                                "steps": [first_line],
                            }
                        ],
                    }
                ]
            }
        )
        payload, trace = run(kit, row_id, injected)
        result = validate(payload)
        if not (result["ok"] and payload["contexts"]):
            failures.append((row_id, trace.route, result["errors"][:2]))
    assert failures == []
