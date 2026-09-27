"""Tests for the gate-scoring harness.

The point of every test here is that the scorecard **fails when it should**. A harness that
always reports green is worse than no harness: it would give false confidence about the one
thing that decides whether we score 60 points or zero.

So each gate gets a deliberately broken input and must reject it, and the whole report must
exit non-zero when anything fails.
"""

from __future__ import annotations

import copy
import json

import pytest

from scripts.run_harness import (
    MAX_VARIATIONS,
    MIN_QUERY_COVERAGE,
    MIN_SCHEMA_VALIDITY,
    MIN_VARIATIONS,
    check_deeplinks,
    check_formatting,
    check_schema_validity,
    check_url_leaks,
    check_variations,
    load_kit_rows,
    load_variations,
    score,
    write_results,
)

HEALTHY = {"status": "ok"}


@pytest.fixture(scope="module")
def good_response():
    return json.loads(open("fixtures/normal.json", encoding="utf-8").read())


@pytest.fixture(scope="module")
def kit_rows():
    return load_kit_rows()


def _records(rows, response, variations=None):
    """One record per kit row, all sharing the same response.

    ``variations is None`` rather than ``variations or ...`` on purpose: an empty list is
    falsy, so the shorter form silently replaced a deliberate zero-variation case with the
    eight-item default and made that test vacuous.
    """
    default = [f"paraphrase {i}" for i in range(8)]
    chosen = default if variations is None else variations
    return [
        {
            "query": row["original_query"],
            "query_variations": list(chosen),
            "response": copy.deepcopy(response),
        }
        for row in rows
    ]


# ------------------------------------------------------------- gates pass when clean -----


def test_all_gates_pass_on_clean_input(kit_rows, good_response):
    report = score(_records(kit_rows, good_response), kit_rows, HEALTHY, {})
    assert report.all_gates_passed, [(g.name, g.detail) for g in report.gates if not g.passed]


# ------------------------------------------------------------------ G2: health -----


@pytest.mark.parametrize(
    "health",
    [{"status": "OK"}, {"status": "ok", "extra": 1}, {}, {"state": "ok"}, {"status": "up"}],
)
def test_g2_fails_on_anything_but_the_exact_body(kit_rows, good_response, health):
    """The gate is an exact match. "OK" is not "ok", and an extra key is not the contract."""
    report = score(_records(kit_rows, good_response), kit_rows, health, {})
    g2 = next(g for g in report.gates if g.name.startswith("G2"))
    assert g2.passed is False


# ------------------------------------------------------------- G3: query coverage -----


def test_g3_fails_when_a_query_is_missing(kit_rows, good_response):
    """Dropping even one of twenty puts coverage at 95%, exactly on the bar; dropping two
    is below it. This is the gate that failed before results.jsonl existed at all."""
    records = _records(kit_rows, good_response)[:-2]
    report = score(records, kit_rows, HEALTHY, {})
    g3 = next(g for g in report.gates if g.name.startswith("G3"))
    assert g3.passed is False
    assert "18/20" in g3.detail


def test_g3_fails_when_a_query_string_was_altered(kit_rows, good_response):
    """G3 is keyed verbatim. Using input.txt's text instead of original_query would silently
    break rows 1 and 17, which differ between the two files."""
    records = _records(kit_rows, good_response)
    for record in records[:3]:
        record["query"] = record["query"].replace("My", "my")
    report = score(records, kit_rows, HEALTHY, {})
    assert next(g for g in report.gates if g.name.startswith("G3")).passed is False


def test_g3_bar_is_the_documented_one():
    assert MIN_QUERY_COVERAGE == 0.95


# ------------------------------------------------------------ G4: schema validity -----


def test_g4_fails_on_a_schema_invalid_response(kit_rows, good_response):
    broken = copy.deepcopy(good_response)
    broken["contexts"][0]["score"] = "not a number"
    report = score(_records(kit_rows, broken), kit_rows, HEALTHY, {})
    assert next(g for g in report.gates if g.name.startswith("G4")).passed is False


def test_g4_counts_partial_validity(kit_rows, good_response):
    """Half valid is below the 90% bar and must fail."""
    broken = copy.deepcopy(good_response)
    broken["contexts"][0]["actions"] = "not a list"
    records = _records(kit_rows, good_response)
    for record in records[:10]:
        record["response"] = copy.deepcopy(broken)
    valid, failures = check_schema_validity(records)
    assert valid == 10 and len(failures) == 10
    assert next(
        g for g in score(records, kit_rows, HEALTHY, {}).gates if g.name.startswith("G4")
    ).passed is False


def test_g4_bar_is_the_documented_one():
    assert MIN_SCHEMA_VALIDITY == 0.90


# --------------------------------------------------------------- G5: URL leaks -----


@pytest.mark.parametrize(
    "leak",
    [
        "Visit samsung.com for more help.",
        "Go to https://example.org/help",
        "Email kidshome.pin@samsung.com to reset the PIN.",
        "See www.samsung.com/support",
    ],
)
def test_g5_fails_on_a_url_anywhere_in_a_step(kit_rows, good_response, leak):
    """One leak zeroes the whole automated score, so this is checked on several shapes --
    including the run-together email that actually appears in kit rows 3, 11 and 17."""
    broken = copy.deepcopy(good_response)
    broken["contexts"][0]["actions"][0]["stepGroups"][0]["steps"].append(leak)
    records = _records(kit_rows, broken)
    assert check_url_leaks(records), f"not detected: {leak!r}"
    assert next(
        g for g in score(records, kit_rows, HEALTHY, {}).gates if g.name.startswith("G5")
    ).passed is False


def test_g5_does_not_flag_the_bixby_uris_we_are_meant_to_emit(kit_rows, good_response):
    """fixtures/normal.json carries real catalog deeplinks. Flagging those would make the
    gate unpassable by a correct response."""
    assert check_url_leaks(_records(kit_rows, good_response)) == []


# --------------------------------------------------- the banned empty response -----


def test_empty_contexts_is_reported_as_a_failure(kit_rows):
    """Schema-valid, leak-free, and worth zero on A4. Banned project-wide, so the harness
    has to catch it rather than pass it silently."""
    records = _records(kit_rows, {"contexts": []})
    report = score(records, kit_rows, HEALTHY, {})
    banned = next(g for g in report.gates if "non-empty" in g.name)
    assert banned.passed is False
    assert report.all_gates_passed is False


# ---------------------------------------------------------------- A2 deeplinks -----


def test_a2_flags_a_uri_that_is_not_in_the_catalog(kit_rows, good_response):
    broken = copy.deepcopy(good_response)
    group = broken["contexts"][0]["actions"][0]["stepGroups"][0]
    group["actionableDeeplink"]["deeplink"] = "bixby://masked/act/deadbeef00"
    result = check_deeplinks(_records(kit_rows, broken))
    assert result["invalid"], "an invented URI was not flagged"


def test_a2_flags_a_validation_uri_used_as_an_actionable_one(kit_rows, good_response):
    """Two allowlists, not one. A val URI in an act slot must be rejected."""
    broken = copy.deepcopy(good_response)
    group = broken["contexts"][0]["actions"][0]["stepGroups"][0]
    group["actionableDeeplink"]["deeplink"] = "bixby://masked/val/266037d0c5"
    assert check_deeplinks(_records(kit_rows, broken))["invalid"]


def test_a2_flags_an_auto_action_with_no_deeplink(kit_rows, good_response):
    """The one automatic A2 deduction."""
    broken = copy.deepcopy(good_response)
    action = broken["contexts"][0]["actions"][0]
    action["category"] = "auto"
    action["stepGroups"][0]["actionableDeeplink"] = None
    assert check_deeplinks(_records(kit_rows, broken))["auto_without_deeplink"]


def test_a2_accepts_the_real_catalog_deeplinks(kit_rows, good_response):
    result = check_deeplinks(_records(kit_rows, good_response))
    assert result["invalid"] == []
    assert result["auto_without_deeplink"] == []
    assert result["with_deeplink"] > 0


# --------------------------------------------------------------- A5 variations -----


@pytest.mark.parametrize("count", [0, 1, 7, 11, 20])
def test_a5_flags_a_count_outside_the_scored_range(kit_rows, good_response, count):
    records = _records(kit_rows, good_response, variations=[f"p{i}" for i in range(count)])
    assert check_variations(records)["out_of_range"]


@pytest.mark.parametrize("count", [8, 9, 10])
def test_a5_accepts_counts_inside_the_range(kit_rows, good_response, count):
    records = _records(kit_rows, good_response, variations=[f"p{i}" for i in range(count)])
    assert check_variations(records)["out_of_range"] == []


def test_a5_flags_duplicated_variations(kit_rows, good_response):
    records = _records(kit_rows, good_response, variations=["same"] * 8)
    assert check_variations(records)["duplicated"]


def test_a5_range_is_the_documented_one():
    assert (MIN_VARIATIONS, MAX_VARIATIONS) == (8, 10)


# -------------------------------------------------------------- A1 formatting -----


def test_a1_reports_a_formatting_violation(kit_rows, good_response):
    broken = copy.deepcopy(good_response)
    broken["contexts"][0]["actions"][0]["description"] = "Too short"
    result = check_formatting(_records(kit_rows, broken))
    assert result["clean"] == 0
    assert result["errors"]


def test_a1_is_clean_on_a_golden_fixture(kit_rows, good_response):
    assert check_formatting(_records(kit_rows, good_response))["clean"] == len(kit_rows)


# ------------------------------------------------------ the results.jsonl artefact -----


def test_written_file_has_the_exact_submission_shape(tmp_path, kit_rows, good_response):
    records = _records(kit_rows, good_response)
    path = tmp_path / "results.jsonl"
    write_results(records, path)

    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == len(kit_rows)
    for line in lines:
        parsed = json.loads(line)
        assert set(parsed) == {"query", "query_variations", "response"}
        assert isinstance(parsed["query"], str) and parsed["query"]
        assert isinstance(parsed["query_variations"], list)
        assert isinstance(parsed["response"], dict)


def test_written_queries_match_the_kit_verbatim(tmp_path, kit_rows, good_response):
    path = tmp_path / "results.jsonl"
    write_results(_records(kit_rows, good_response), path)
    written = [json.loads(l)["query"] for l in path.read_text(encoding="utf-8").splitlines()]
    assert written == [row["original_query"] for row in kit_rows]


def test_written_file_survives_the_non_ascii_row(tmp_path, kit_rows, good_response):
    """row_11 contains a UTF-8 em dash. Writing it wrong corrupts a graded query string."""
    path = tmp_path / "results.jsonl"
    write_results(_records(kit_rows, good_response), path)
    reread = [json.loads(l)["query"] for l in path.read_text(encoding="utf-8").splitlines()]
    dashed = [q for q in reread if "—" in q]
    assert dashed, "the em-dash row did not survive the round trip"


# --------------------------------------------------------------- the fixture -----


def test_the_committed_paraphrase_fixture_covers_every_row(kit_rows):
    """If this fails, building results.jsonl would silently fall back to live Gemini calls."""
    variations = load_variations(kit_rows)
    missing = [row["id"] for row in kit_rows if row["id"] not in variations]
    assert missing == []
    for row_id, items in variations.items():
        assert MIN_VARIATIONS <= len(items) <= MAX_VARIATIONS, f"{row_id} has {len(items)}"
        assert len(set(items)) == len(items), f"{row_id} has duplicates"
