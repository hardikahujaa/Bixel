"""Tests for validate(). Owner: M2.

The first test is the acceptance test named in the module docstring: validate() must
REJECT Samsung's own sample_output.json, because its two Action.description values are 9
and 12 words against a 5-7 word rule and its goal has no trailing period. A validator
that passes that file is not checking anything.

Every rule then gets a test that violates it in isolation, starting from a known-good
fixture, so a passing suite means each rule is actually wired up rather than accidentally
satisfied.
"""

import copy
import json

import pytest

from app.validator import validate


@pytest.fixture(scope="module")
def good():
    """A known-valid response built from real catalog entries."""
    return json.loads(open("fixtures/normal.json", encoding="utf-8").read())


def _broken(good, mutate):
    payload = copy.deepcopy(good)
    mutate(payload)
    return payload


def _errors_mentioning(result, needle):
    return [e for e in result["errors"] if needle in e]


# ------------------------------------------------------------- the acceptance test -----


def test_rejects_samsungs_own_sample_output():
    sample = json.loads(open("student_kit/sample_output.json", encoding="utf-8").read())
    result = validate(sample["response"])
    assert result["ok"] is False
    assert _errors_mentioning(result, "goal does not match"), "should catch the missing period"
    assert len(_errors_mentioning(result, "must be 5-7 words")) == 2, "should catch both 9 and 12 word descriptions"


def test_banned_empty_contexts_is_rejected():
    """Schema-valid but scores zero on A4. Explicitly banned by team decision."""
    result = validate({"contexts": []})
    assert result["ok"] is False
    assert _errors_mentioning(result, "banned")


# ----------------------------------------------------------------- happy paths -----


@pytest.mark.parametrize("name", ["normal", "dummy_positive", "critical"])
def test_all_three_golden_fixtures_pass(name):
    result = validate(json.loads(open(f"fixtures/{name}.json", encoding="utf-8").read()))
    assert result["ok"] is True, result["errors"]


def test_the_live_endpoint_response_passes():
    """Whatever /v1/troubleshoot actually serves must satisfy the rules.

    extract() validates internally before returning, so this is a check that
    the wiring in app/main.py (cache included) does not lose or mutate
    anything on the way out -- not a re-test of extract() itself, which
    backend/extract/tests already covers in depth.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).post(
        "/v1/troubleshoot",
        json={
            "query": "My screen is black",
            "siis_response": {"title": "Blank screen", "content": "Some content"},
        },
    )
    result = validate(response.json())
    assert result["ok"] is True, result["errors"]


# ------------------------------------------------------------ one rule at a time -----


def test_goal_must_end_with_a_period(good):
    payload = _broken(good, lambda p: p["contexts"][0].update(
        goal="Follow these steps to perform this Screen Damage Troubleshooting"))
    assert _errors_mentioning(validate(payload), "goal does not match")


def test_goal_must_use_troubleshooting_or_configuration(good):
    payload = _broken(good, lambda p: p["contexts"][0].update(
        goal="Follow these steps to perform this Screen Damage Repair."))
    assert _errors_mentioning(validate(payload), "goal does not match")


def test_goal_configuration_variant_is_accepted(good):
    payload = _broken(good, lambda p: p["contexts"][0].update(
        goal="Follow these steps to perform this Display Configuration."))
    assert validate(payload)["ok"] is True


@pytest.mark.parametrize("title,ok", [
    ("One", False), ("Two words", True), ("Three word title", True),
    ("Four word long title", False), ("", False),
])
def test_title_word_count(good, title, ok):
    payload = _broken(good, lambda p: p["contexts"][0].update(title=title))
    assert validate(payload)["ok"] is ok


@pytest.mark.parametrize("score,ok", [
    (0.0, True), (1.0, True), (0.5, True), (-0.1, False), (1.1, False), ("high", False), (None, False),
])
def test_score_range_and_type(good, score, ok):
    payload = _broken(good, lambda p: p["contexts"][0].update(score=score))
    assert validate(payload)["ok"] is ok


@pytest.mark.parametrize("description,ok", [
    ("It will back up your device data", True),          # 7
    ("It will back up data", True),                       # 5
    ("It will back up", False),                           # 4
    ("It will back up your device data now", False),      # 8
    ("Backs up your device data safely", False),          # right length, wrong prefix
    ("it will back up your device data", False),          # lowercase prefix
])
def test_description_word_count_and_prefix(good, description, ok):
    payload = _broken(good, lambda p: p["contexts"][0]["actions"][0].update(description=description))
    assert validate(payload)["ok"] is ok


def test_absent_category_is_rejected(good):
    """schema.py silently defaults category to 'manual', hiding a dropped field."""
    payload = _broken(good, lambda p: p["contexts"][0]["actions"][0].pop("category"))
    assert _errors_mentioning(validate(payload), "category is absent")


@pytest.mark.parametrize("category,ok", [
    ("auto", True), ("manual", True), ("critical", True), ("automatic", False), (None, False),
])
def test_category_vocabulary(good, category, ok):
    payload = copy.deepcopy(good)
    payload["contexts"][0]["actions"][0]["category"] = category
    # an auto action needs its deeplink; manual/critical keep the one they have, which is fine
    assert validate(payload)["ok"] is ok


def test_auto_action_without_a_deeplink_is_rejected(good):
    """The single automatic A2 deduction."""
    def mutate(p):
        action = p["contexts"][0]["actions"][0]
        action["category"] = "auto"
        action["stepGroups"][0]["actionableDeeplink"] = None
    assert _errors_mentioning(validate(_broken(good, mutate)), "category 'auto' but no stepGroup")


def test_manual_action_without_a_deeplink_is_fine(good):
    def mutate(p):
        action = p["contexts"][0]["actions"][0]
        action["category"] = "manual"
        action["stepGroups"][0]["actionableDeeplink"] = None
        action["stepGroups"][0]["validationDeeplink"] = None
    assert validate(_broken(good, mutate))["ok"] is True


@pytest.mark.parametrize("steps", [[], [""], ["  "], [None]])
def test_steps_must_be_non_empty(good, steps):
    payload = _broken(good, lambda p: p["contexts"][0]["actions"][0]["stepGroups"][0].update(steps=steps))
    assert validate(payload)["ok"] is False


def test_empty_actions_list_is_rejected(good):
    payload = _broken(good, lambda p: p["contexts"][0].update(actions=[]))
    assert _errors_mentioning(validate(payload), "actions is missing or empty")


def test_empty_step_groups_is_rejected(good):
    payload = _broken(good, lambda p: p["contexts"][0]["actions"][0].update(stepGroups=[]))
    assert _errors_mentioning(validate(payload), "stepGroups is missing or empty")


# --------------------------------------------------------------- URIs and URLs -----


def test_invented_actionable_uri_is_rejected(good):
    """The whole point of the allowlist: a hallucinated URI must never pass."""
    payload = _broken(good, lambda p: p["contexts"][0]["actions"][0]["stepGroups"][0]
                      ["actionableDeeplink"].update(deeplink="bixby://masked/act/deadbeef00"))
    assert _errors_mentioning(validate(payload), "not in the catalog")


def test_validation_uri_cannot_be_used_as_an_actionable_uri(good):
    """Act and val URIs are separate namespaces with zero overlap -- two allowlists, not one."""
    payload = _broken(good, lambda p: p["contexts"][0]["actions"][0]["stepGroups"][0]
                      ["actionableDeeplink"].update(deeplink="bixby://masked/val/266037d0c5"))
    assert _errors_mentioning(validate(payload), "not in the catalog")


def test_invented_validation_uri_is_rejected(good):
    payload = _broken(good, lambda p: p["contexts"][0]["actions"][0]["stepGroups"][0]
                      ["validationDeeplink"].update(deeplink="bixby://masked/val/deadbeef00"))
    assert _errors_mentioning(validate(payload), "not a known validation URI")


def test_validation_deeplink_requires_a_key(good):
    payload = _broken(good, lambda p: p["contexts"][0]["actions"][0]["stepGroups"][0]
                      ["validationDeeplink"].update(key=""))
    assert _errors_mentioning(validate(payload), "key is required")


@pytest.mark.parametrize("leak", [
    "Visit samsung.com for this Screen Damage Troubleshooting.",
    "Follow these steps to perform this www.samsung.com Troubleshooting.",
])
def test_url_in_the_goal_is_rejected(good, leak):
    payload = _broken(good, lambda p: p["contexts"][0].update(goal=leak))
    assert _errors_mentioning(validate(payload), "URL-shaped text")


def test_url_in_a_step_is_rejected(good):
    payload = _broken(good, lambda p: p["contexts"][0]["actions"][0]["stepGroups"][0]["steps"]
                      .append("Then email kidshome.pin@samsung.com for help."))
    assert _errors_mentioning(validate(payload), "URL-shaped text")


def test_dummy_positive_requires_our_own_short_description():
    """Its own qna_description tells us to write description and message ourselves,
    5-7 words each."""
    payload = json.loads(open("fixtures/dummy_positive.json", encoding="utf-8").read())
    assert validate(payload)["ok"] is True

    payload["contexts"][0]["actions"][0]["stepGroups"][0]["actionableDeeplink"]["description"] = (
        "Generic placeholder for a Settings screen that has no dedicated entry"
    )
    assert _errors_mentioning(validate(payload), "dummy_positive")


# ------------------------------------------------------------------ malformed -----


@pytest.mark.parametrize("bad", [None, "x", 5, [], {"contexts": "no"}, {"contexts": {}}, {}])
def test_malformed_response_is_rejected_without_raising(bad):
    result = validate(bad)
    assert result["ok"] is False
    assert result["errors"]


def test_all_errors_are_collected_not_just_the_first(good):
    """A caller should see everything wrong in one pass."""
    def mutate(p):
        ctx = p["contexts"][0]
        ctx["goal"] = "wrong"
        ctx["title"] = "One"
        ctx["score"] = 5.0
        ctx["actions"][0]["description"] = "too short"
    result = validate(_broken(good, mutate))
    assert len(result["errors"]) >= 4, result["errors"]
