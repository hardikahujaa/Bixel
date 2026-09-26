"""Tests for the deterministic fallback.

The fallback carries the project's "contexts is never empty" guarantee, so the bar is
higher than usual: it has to produce a response that passes the real ``validate()`` for
every kit row and for deliberately broken input, without an LLM anywhere in the picture.
"""

from __future__ import annotations

import json

import pytest

from app.sanitizer import sanitize_response
from app.validator import validate
from backend.extract.fallback import (
    build_fallback,
    make_description,
    make_goal,
    make_title,
    split_sections,
)


@pytest.fixture(scope="module")
def kit_rows():
    payload = json.loads(open("student_kit/siis_responses.json", encoding="utf-8").read())
    return payload["responses"]


# ---------------------------------------------------------------- the guarantee -----


def test_every_kit_row_produces_a_valid_fallback(kit_rows):
    """20 for 20. If this ever fails, the never-empty guarantee has a hole in it."""
    failures = []
    for row in kit_rows:
        payload = sanitize_response(build_fallback(row["original_query"], row["siis_response"]))
        result = validate(payload)
        if not result["ok"]:
            failures.append((row["id"], result["errors"][:3]))
    assert failures == []


@pytest.mark.parametrize(
    "query,siis",
    [
        ("screen black", {"title": "", "content": ""}),
        ("screen black", {"title": "Blank display", "content": ""}),
        ("", {"title": "", "content": ""}),
        ("screen black", {}),
        ("screen black", None),
        (None, None),
        ("screen black", {"title": None, "content": None}),
        ("screen black", {"content": "no headings here, just a single line of prose."}),
        ("x" * 5000, {"title": "t", "content": "## H\nTap Settings."}),
    ],
)
def test_broken_input_still_yields_a_valid_non_empty_response(query, siis):
    payload = build_fallback(query, siis)
    assert payload["contexts"], "returned empty contexts -- banned project-wide"
    result = validate(sanitize_response(payload))
    assert result["ok"] is True, result["errors"]


def test_never_returns_empty_contexts_on_pure_garbage():
    for siis in [{"title": "!!!", "content": "!!! ??? ..."}, {"title": "   ", "content": "\n\n\n"}]:
        payload = build_fallback("???", siis)
        assert len(payload["contexts"]) >= 1
        assert payload["contexts"][0]["actions"], "a goal with no actions is invalid"


# -------------------------------------------------------------------- grounding -----


def test_every_step_is_copied_from_the_supplied_content(kit_rows):
    """The fallback is grounded by construction -- it only copies lines it was given.

    This is the property that makes it safe on A4: it cannot answer from general
    knowledge because it has no source of knowledge other than the document.
    """
    for row in kit_rows:
        content = row["siis_response"]["content"]
        normalised_source = " ".join(content.split())
        payload = build_fallback(row["original_query"], row["siis_response"])
        for goal in payload["contexts"]:
            for action in goal["actions"]:
                for group in action["stepGroups"]:
                    for step in group["steps"]:
                        if step == "Review the guidance for this device issue.":
                            continue  # documented last-resort filler
                        if step.startswith("Contact Samsung Support to arrange"):
                            continue  # documented last-resort filler
                        assert " ".join(step.split()) in normalised_source, (
                            f"{row['id']}: step not found in source: {step[:70]!r}"
                        )


def test_fallback_proposes_no_deeplinks(kit_rows):
    """It has done no matching, so claiming a deeplink would be a fabrication. Every
    action is therefore 'manual', which is also why it can never trip the
    'auto action without a deeplink' rule."""
    for row in kit_rows[:6]:
        payload = build_fallback(row["original_query"], row["siis_response"])
        for goal in payload["contexts"]:
            for action in goal["actions"]:
                assert action["category"] == "manual"
                for group in action["stepGroups"]:
                    assert group["actionableDeeplink"] is None
                    assert group["validationDeeplink"] is None


def test_score_is_honestly_low(kit_rows):
    """This path did no reasoning about the query, and the score should say so."""
    payload = build_fallback(kit_rows[0]["original_query"], kit_rows[0]["siis_response"])
    assert 0.0 <= payload["contexts"][0]["score"] <= 0.5


# ------------------------------------------------------- the formatting helpers -----


@pytest.mark.parametrize(
    "heading",
    [
        "Step 1: Check for Physical Damage and Liquid Exposure",
        "Step 2: Force a Restart",
        "Step 3: Charge the Device",
        "5. Touch Sensitivity Setting",
        "6. Perform a Factory Data Reset",
        "Mirror Your TV with Smart View",
        "Customize the Edge panel",
        "7. Contact Samsung Support",
        "Glossary",
        "a",
        "",
        "!!!",
        "The",
    ],
)
def test_description_is_always_five_to_seven_words_starting_with_it_will(heading):
    description = make_description(heading)
    assert description.startswith("It will")
    assert 5 <= len(description.split()) <= 7, description


def test_description_leads_with_the_headings_own_verb():
    """Without this the phrasing collapses into non-English that still passes the rules.

    "Check for Physical Damage" once produced "It will physical damage liquid exposure".
    """
    assert make_description("Step 1: Check for Physical Damage") .startswith("It will check")
    assert "force" in make_description("Step 2: Force a Restart")
    assert "clear" in make_description("Step 4: Clear the Email App's Cache")


@pytest.mark.parametrize(
    "source",
    ["Blank or black display on a Samsung phone or tablet", "a", "", "!!!", "The the the",
     "Touchscreen issues on a Galaxy phone or tablet"],
)
def test_title_is_always_two_or_three_words(source):
    assert 2 <= len(make_title(source).split()) <= 3, make_title(source)


@pytest.mark.parametrize("source", ["Blank display", "", "!!!", "Screen does not rotate"])
def test_goal_always_matches_the_required_pattern(source):
    goal = make_goal(source)
    assert goal.startswith("Follow these steps to perform this ")
    assert goal.endswith("Troubleshooting.")


# --------------------------------------------------------------- section splitting -----


def test_splits_on_double_hash_headings():
    sections = split_sections("## One\nTap A.\nTap B.\n## Two\nTap C.")
    assert [h for h, _ in sections] == ["One", "Two"]
    assert sections[0][1] == ["Tap A.", "Tap B."]


def test_content_without_headings_still_yields_a_section():
    """Several kit rows are flat prose. Returning nothing here would defeat the purpose."""
    sections = split_sections("Just one line of prose with no headings at all.")
    assert len(sections) == 1
    assert sections[0][1]


def test_empty_content_yields_no_sections():
    assert split_sections("") == []
    assert split_sections(None) == []
