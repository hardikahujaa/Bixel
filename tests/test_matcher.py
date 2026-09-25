import json
from pathlib import Path

from app.matcher import (
    GENERIC_MESSAGE_PHRASES,
    CatalogIndex,
    _message_phrase,
    match_deeplinks,
)

LABELS_PATH = Path(__file__).resolve().parent.parent / "app" / "matcher_labels.json"


def _load_labels():
    with open(LABELS_PATH, encoding="utf-8") as f:
        return json.load(f)["labels"]


def test_matcher_precision_and_recall_on_labeled_set():
    """Regression guard for the Day 1 hand-labeled precision measurement
    (Claude.md Day 1, M1) -- see scripts/measure_matcher_precision.py for
    the full sweep and per-example detail. Fails loudly if a future change
    to the phrase gate or denylist quietly reintroduces false positives."""
    labels = _load_labels()
    tp = fp = tn = fn = 0
    for label in labels:
        acceptable = set(label["acceptable_catalog_ids"])
        candidates = {r["catalog_id"] for r in match_deeplinks(label["step_text"])}
        if acceptable:
            tp += 1 if acceptable & candidates else 0
            fn += 0 if acceptable & candidates else 1
        else:
            fp += 1 if candidates else 0
            tn += 0 if candidates else 1

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")

    assert precision >= 0.9, f"precision regressed to {precision:.2f} (tp={tp} fp={fp})"
    assert recall >= 0.8, f"recall regressed to {recall:.2f} (tp={tp} fn={fn})"


def test_abstains_on_uncovered_feature():
    assert match_deeplinks("Navigate to Settings. Tap Apps. Select your email app. Tap Storage. Tap Clear cache.") == []


def test_matches_covered_feature():
    results = match_deeplinks(
        "To turn off this feature, navigate to Settings, tap Display, and then tap the switch next to Touch sensitivity to disable it."
    )
    assert any(r["catalog_id"] == "DL-0125" for r in results)


# --- catalog loading -------------------------------------------------------


def test_catalog_loads_all_578_entries():
    assert len(CatalogIndex().entries) == 578


def test_catalog_entries_have_unique_ids():
    ids = [e["id"] for e in CatalogIndex().entries]
    assert len(ids) == len(set(ids))


# --- _message_phrase: verb-prefix stripping --------------------------------


def test_message_phrase_strips_each_known_verb_prefix():
    assert _message_phrase({"message": "Enable Touch sensitivity"}) == "touch sensitivity"
    assert _message_phrase({"message": "Disable Touch sensitivity"}) == "touch sensitivity"
    assert _message_phrase({"message": "View WiFi Settings"}) == "wifi settings"
    assert _message_phrase({"message": "Adjust Timeout"}) == "timeout"
    assert _message_phrase({"message": "Check Screen Timeout"}) == "screen timeout"


def test_message_phrase_prefix_matching_is_case_insensitive():
    assert _message_phrase({"message": "enable Touch sensitivity"}) == "touch sensitivity"
    assert _message_phrase({"message": "DISABLE Touch sensitivity"}) == "touch sensitivity"


def test_message_phrase_handles_missing_or_empty_message():
    assert _message_phrase({}) == ""
    assert _message_phrase({"message": ""}) == ""
    assert _message_phrase({"message": None}) == ""


def test_message_phrase_only_strips_a_leading_verb_not_mid_string():
    # "View" only counts as the gate verb when it leads; a literal
    # occurrence elsewhere in the message must survive untouched.
    assert _message_phrase({"message": "Preview mode"}) == "preview mode"


# --- generic-phrase denylist ------------------------------------------------


def test_generic_denylist_entries_are_lowercase():
    assert all(phrase == phrase.lower() for phrase in GENERIC_MESSAGE_PHRASES)


def test_generic_denylist_suppresses_bare_hardware_nouns():
    # "Adjust Volume" / "View Side button" should never fire just because a
    # force-restart instruction happens to mention those generic nouns.
    results = match_deeplinks("Press and hold the Volume down button (or Side button) to restart.")
    assert results == []


# --- match_deeplinks: input handling ----------------------------------------


def test_match_is_case_insensitive():
    step = "go to settings, tap display, then tap touch sensitivity to disable it."
    lower_ids = {r["catalog_id"] for r in match_deeplinks(step)}
    upper_ids = {r["catalog_id"] for r in match_deeplinks(step.upper())}
    assert lower_ids
    assert lower_ids == upper_ids


def test_empty_step_text_abstains():
    assert match_deeplinks("") == []


def test_whitespace_only_step_text_abstains():
    assert match_deeplinks("   \n\t  ") == []


def test_unrelated_text_abstains():
    assert match_deeplinks("The quick brown fox jumps over the lazy dog.") == []


def test_top_k_limits_result_count():
    step = "go to Settings, tap Display, then tap the switch next to Touch sensitivity."
    assert len(match_deeplinks(step, top_k=1)) <= 1


def test_ctx_argument_is_accepted_and_does_not_change_the_result():
    step = "go to Settings, tap Display, then tap the switch next to Touch sensitivity to disable it."
    assert match_deeplinks(step) == match_deeplinks(step, ctx={"anything": "goes"})


def test_handles_utf8_em_dash_without_crashing():
    # student_kit/siis_responses.json row_11 has a literal em dash;
    # KIT_NOTES.md explicitly warns this corrupts if not handled as UTF-8.
    result = match_deeplinks("Navigate to Settings — then tap Display.")
    assert isinstance(result, list)


# --- match_deeplinks: output shape ------------------------------------------


def test_result_shape_has_exactly_the_expected_keys():
    step = "go to Settings, tap Display, then tap the switch next to Touch sensitivity to disable it."
    results = match_deeplinks(step)
    assert results
    for r in results:
        assert set(r.keys()) == {"catalog_id", "score", "entry"}
        assert isinstance(r["score"], float)
        assert 0.0 <= r["score"] <= 1.0 + 1e-9
        assert r["entry"]["id"] == r["catalog_id"]


def test_results_are_sorted_by_score_descending():
    step = "go to Settings, tap Display, then tap the switch next to Touch sensitivity."
    scores = [r["score"] for r in match_deeplinks(step, top_k=5)]
    assert scores == sorted(scores, reverse=True)


def test_no_catalog_entry_is_returned_twice_in_one_call():
    step = "go to Settings, tap Display, then tap the switch next to Touch sensitivity."
    ids = [r["catalog_id"] for r in match_deeplinks(step)]
    assert len(ids) == len(set(ids))
