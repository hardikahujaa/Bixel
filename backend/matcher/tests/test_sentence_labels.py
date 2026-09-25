"""Score this matcher against Siddhant's 29 sentence-level labels.

Two jobs here. First, verify the dataset itself is trustworthy -- every label must trace
back to the SIIS row it claims to come from, and every expected catalog id must exist.
Second, lock in the measured performance on short sentence input as a regression floor,
separately from the group-level numbers in test_matcher.py.

The recall gap is deliberately asserted rather than hidden: this matcher reaches 0.714 on
this dataset while Siddhant's deleted phrase-gate matcher reached 0.857, at equal
precision. That is a known, measured weakness on short text. If someone improves it, the
assertion below fails and tells them to raise the floor.
"""

from __future__ import annotations

import pytest

from backend.matcher.catalog import load_catalog
from backend.matcher.matcher import DEFAULT_THRESHOLDS, DeeplinkMatcher
from backend.matcher.sentence_labels import (
    counts,
    load_sentence_labels,
    siis_content_by_row,
    traces_to_source,
)

# Measured on this dataset at DEFAULT_THRESHOLDS. Group-level numbers live in
# test_matcher.py; these are the sentence-level ones.
EXPECTED_PRECISION = 1.0
EXPECTED_RECALL = 0.714          # 5 of 7
EXPECTED_ABSTENTION = 1.0        # 22 of 22

# Siddhant's phrase-gate matcher scored this on the same dataset. Kept as a written
# reference point so the gap is not forgotten.
PHRASE_GATE_RECALL = 0.857       # 6 of 7


@pytest.fixture(scope="module")
def matcher():
    instance = DeeplinkMatcher(DEFAULT_THRESHOLDS)
    instance.thresholds = DEFAULT_THRESHOLDS
    return instance


# ------------------------------------------------------------ dataset integrity -----


def test_dataset_shape():
    assert counts() == {"total": 29, "match": 7, "none": 22}


def test_every_label_row_id_exists_in_the_kit():
    rows = siis_content_by_row()
    missing = sorted({l.row_id for l in load_sentence_labels()} - set(rows))
    assert missing == [], f"labels reference row ids not in siis_responses.json: {missing}"


def test_every_label_traces_back_to_its_source_row():
    """A label whose text is not in the source is mistyped or invented.

    26 of 29 are byte-exact; the other 3 are whitespace-trimmed clauses, which is why the
    check normalises whitespace rather than demanding an exact substring.
    """
    failures = [l.row_id + ": " + l.step_text[:60] for l in load_sentence_labels() if not traces_to_source(l)]
    assert failures == [], f"labels not found in their source row: {failures}"


def test_every_expected_catalog_id_exists():
    known = {entry.id for entry in load_catalog()}
    referenced = {cid for l in load_sentence_labels() for cid in l.acceptable_ids}
    assert referenced - known == set(), f"labels reference unknown ids: {sorted(referenced - known)}"


def test_positive_labels_carry_a_reason():
    """Every label should say why, so a teammate can challenge it."""
    for label in load_sentence_labels():
        if label.expects_match:
            assert label.note.strip(), f"{label.row_id} has an expected id but no note"


# --------------------------------------------------------------- measured scores -----


def _score(matcher):
    tp = fp = tn = fn = 0
    misses, false_positives = [], []
    for label in load_sentence_labels():
        returned = {c.catalog_id for c in matcher.match(label.step_text)}
        if label.expects_match:
            if returned & label.acceptable_ids:
                tp += 1
            else:
                fn += 1
                misses.append((sorted(label.acceptable_ids), sorted(returned), label.step_text[:64]))
                if returned:
                    # a wrong entry returned is a wrong deeplink, not just a miss
                    fp += 1
        else:
            if returned:
                fp += 1
                false_positives.append((sorted(returned), label.step_text[:64]))
            else:
                tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    abstention = tn / (tn + fp) if tn + fp else 0.0
    return precision, recall, abstention, tp, fp, tn, fn, misses, false_positives


def test_precision_is_perfect_on_sentence_level_input(matcher):
    precision, _, _, _, fp, _, _, _, false_positives = _score(matcher)
    assert fp == 0, f"false positives: {false_positives}"
    assert precision == EXPECTED_PRECISION


def test_abstains_on_every_no_match_label(matcher):
    """22 sentences with no valid catalog entry, several of them deliberate lexical traps."""
    _, _, abstention, _, _, tn, _, _, false_positives = _score(matcher)
    assert false_positives == [], f"should have abstained: {false_positives}"
    assert tn == 22
    assert abstention == EXPECTED_ABSTENTION


def test_recall_has_not_regressed(matcher):
    _, recall, _, tp, _, _, fn, misses, _ = _score(matcher)
    assert round(recall, 3) >= EXPECTED_RECALL, (
        f"sentence-level recall dropped to {recall:.3f} (tp={tp} fn={fn}); misses: {misses}"
    )


def test_the_known_misses_are_still_the_expected_two(matcher):
    """Pins the specific weaknesses so a future change is visibly a change.

    DL-0313 (Wi-Fi) is a catalog data problem both matchers hit: six entries share a
    'WiFi' message while describing different features. DL-0169 (Navigation bar) is one
    the phrase gate caught and this matcher does not.
    """
    _, _, _, _, _, _, _, misses, _ = _score(matcher)
    missed_ids = sorted(ids[0] for ids, _returned, _text in misses)
    assert missed_ids == ["DL-0169", "DL-0313"], f"misses changed: {misses}"


def test_phrase_gate_reference_point_is_still_higher(matcher):
    """Documents, in an executable place, that the deleted matcher beat this one here.

    Not a failure -- a recorded gap. If this matcher ever exceeds 0.857 on this dataset,
    this test fails and the reference point should be retired.
    """
    _, recall, _, _, _, _, _, _, _ = _score(matcher)
    assert recall <= PHRASE_GATE_RECALL, (
        f"this matcher now reaches {recall:.3f}, at or above the phrase gate's "
        f"{PHRASE_GATE_RECALL}. Retire PHRASE_GATE_RECALL and raise EXPECTED_RECALL."
    )


def test_no_returned_entry_is_mutated(matcher):
    """The raw catalog entry contract holds on this dataset too."""
    by_id = {entry.id: entry.raw for entry in matcher.entries}
    for label in load_sentence_labels():
        for candidate in matcher.match(label.step_text):
            assert candidate.entry == by_id[candidate.catalog_id]
