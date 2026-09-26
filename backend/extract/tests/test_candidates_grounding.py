"""Tests for the candidate builder and the grounding check.

These two carry the pipeline's two hardest guarantees, so they are tested apart from the
pipeline: the candidate builder is what makes an invented URI impossible, and grounding is
what stops the model answering from its own knowledge of Samsung phones.
"""

from __future__ import annotations

import json

import pytest

from backend.extract.candidates import (
    MAX_OPTIONS_PER_SECTION,
    MAX_SECTIONS,
    build_candidates,
    render_for_prompt,
)
from backend.extract.fallback import build_fallback
from backend.extract.grounding import (
    OVERLAP_THRESHOLD,
    ground_response,
    is_grounded,
    source_lines,
)


@pytest.fixture(scope="module")
def kit():
    payload = json.loads(open("student_kit/siis_responses.json", encoding="utf-8").read())
    return {row["id"]: row for row in payload["responses"]}


@pytest.fixture(scope="module")
def touch_content(kit):
    return kit["row_21"]["siis_response"]["content"]


class _Candidate:
    def __init__(self, catalog_id, entry, score=0.9, polarity=0):
        self.catalog_id = catalog_id
        self.entry = entry
        self.score = score
        self.polarity = polarity


ENTRY = {
    "id": "DL-0125",
    "deeplink": "bixby://masked/act/abc1234567",
    "message": "Disable Touch sensitivity",
    "description": "Opens the touch sensitivity settings page in device Settings on the device.",
    "qna_description": "Increases touch sensitivity for better screen response.",
    "originalType": "onClickURL",
    "validation": {"deeplink": "bixby://masked/val/def1234567", "key": "Touch sensitivity"},
}


# ------------------------------------------------------------ candidates -----


def test_no_uri_appears_anywhere_in_the_prompt_block(kit):
    """The central guarantee. If a URI can reach the prompt, the model can copy it."""
    def matcher(text, heading=None):
        return [_Candidate("DL-0125", ENTRY)]

    sections = build_candidates(kit["row_21"]["siis_response"], matcher=matcher)
    rendered = render_for_prompt(sections)
    assert "bixby://" not in rendered
    assert ENTRY["deeplink"] not in rendered
    assert "DL-0125" not in rendered, "even the catalog id stays server-side"
    # but the human-readable label does appear, so the model has something to choose on
    assert "Disable Touch sensitivity" in rendered


def test_options_are_numbered_from_one_per_section(kit):
    def matcher(text, heading=None):
        return [_Candidate("A", ENTRY), _Candidate("B", dict(ENTRY, id="B"))]

    sections = build_candidates(kit["row_21"]["siis_response"], matcher=matcher)
    for section in sections:
        assert [o.number for o in section.options] == [1, 2]


def test_option_lookup_resolves_the_models_answer(kit):
    def matcher(text, heading=None):
        return [_Candidate("DL-0125", ENTRY)]

    section = build_candidates(kit["row_21"]["siis_response"], matcher=matcher)[0]
    assert section.option_by_number(1).catalog_id == "DL-0125"
    assert section.option_by_number("1").catalog_id == "DL-0125"


@pytest.mark.parametrize("junk", [None, 0, 2, 99, -1, "none", "None", "two", "", [], {}, 1.5])
def test_unrecognised_answers_resolve_to_none(kit, junk):
    """An out-of-range or junk index must mean "no deeplink", never an arbitrary entry."""
    def matcher(text, heading=None):
        return [_Candidate("DL-0125", ENTRY)]

    section = build_candidates(kit["row_21"]["siis_response"], matcher=matcher)[0]
    assert section.option_by_number(junk) is None


def test_a_matcher_that_raises_degrades_to_no_candidates(kit):
    """A matcher failure must not fail the request -- no candidates just means no deeplink."""
    def broken(text, heading=None):
        raise RuntimeError("index missing")

    sections = build_candidates(kit["row_21"]["siis_response"], matcher=broken)
    assert sections, "sections should still be built"
    assert all(section.options == () for section in sections)


def test_sections_and_options_are_capped(kit):
    """One kit document has 11 sections; an unbounded prompt costs latency for no gain."""
    def many(text, heading=None):
        return [_Candidate(str(i), dict(ENTRY, id=str(i))) for i in range(10)]

    mirror = next(r for r in kit.values() if "Screen mirroring" in r["siis_response"]["title"])
    sections = build_candidates(mirror["siis_response"], matcher=many)
    assert len(sections) <= MAX_SECTIONS
    assert all(len(s.options) <= MAX_OPTIONS_PER_SECTION for s in sections)


def test_empty_or_broken_document_yields_no_sections():
    for siis in [{}, None, {"content": ""}, {"content": None}, "not a dict"]:
        assert build_candidates(siis, matcher=lambda t, heading=None: []) == []


def test_sections_carry_the_lines_the_model_may_use(kit):
    sections = build_candidates(
        kit["row_21"]["siis_response"], matcher=lambda t, heading=None: []
    )
    assert sections
    rendered = render_for_prompt(sections)
    assert "Lines you may use as steps" in rendered
    assert "you must answer none" in rendered, "a section with no options must say so"


# ------------------------------------------------------------- grounding -----


def test_verbatim_lines_are_grounded(touch_content):
    for line in source_lines(touch_content)[:12]:
        assert is_grounded(line, touch_content), line[:60]


def test_every_fallback_step_is_grounded(kit):
    """The fallback only copies lines, so all 20 rows must ground completely.

    This is also the calibration set behind OVERLAP_THRESHOLD.
    """
    for row in kit.values():
        content = row["siis_response"]["content"]
        payload = build_fallback(row["original_query"], row["siis_response"])
        _, report = ground_response(payload, content)
        assert report.dropped == [], f"{row['id']}: {report.dropped[:2]}"


@pytest.mark.parametrize(
    "invented",
    [
        "Open Settings and tap Battery and device care.",
        "Enable Adaptive brightness under Display settings.",
        "Navigate to Settings, tap Connections, then tap Bluetooth.",
        "Install the latest One UI update from Software update.",
        "Tap Accounts and backup, then Samsung Cloud.",
        "Contact your carrier to activate the SIM card.",
        "Factory reset the device from General management.",
    ],
)
def test_plausible_invented_instructions_are_not_grounded(invented, touch_content):
    """These are exactly what a model produces from memory: well-formed, plausible, and
    absent from the document. All seven must fail at the calibrated threshold."""
    assert not is_grounded(invented, touch_content)


def test_threshold_is_the_calibrated_value():
    """0.65 is the lowest value that leaked none of the seven invented cases above while
    keeping all 83 real steps. Changing it needs re-running that measurement."""
    assert OVERLAP_THRESHOLD == 0.65


@pytest.mark.parametrize("bad", ["", "   ", None, 123, [], {}])
def test_malformed_steps_are_not_grounded(bad, touch_content):
    assert not is_grounded(bad, touch_content)


def test_nothing_is_grounded_against_empty_content():
    assert not is_grounded("Tap Settings.", "")
    assert not is_grounded("Tap Settings.", None)


def test_ground_response_prunes_upwards():
    """An emptied group drops, then its action, then its goal -- so the caller can tell
    that everything went and fall back."""
    payload = {
        "contexts": [
            {
                "goal": "g",
                "title": "t t",
                "score": 0.5,
                "actions": [
                    {
                        "actionName": "a",
                        "description": "It will do a thing",
                        "category": "manual",
                        "stepGroups": [{"steps": ["completely invented instruction here"]}],
                    }
                ],
            }
        ]
    }
    grounded, report = ground_response(payload, "## H\nTap Settings.")
    assert grounded["contexts"] == []
    assert report.everything_dropped
    assert report.dropped_groups == 1 and report.dropped_actions == 1 and report.dropped_goals == 1


def test_ground_response_survives_malformed_payloads():
    for payload in [None, "x", 5, [], {}, {"contexts": None}, {"contexts": ["x"]}]:
        grounded, _ = ground_response(payload, "## H\nTap Settings.")
        assert isinstance(grounded, dict)
        assert isinstance(grounded.get("contexts"), list)
