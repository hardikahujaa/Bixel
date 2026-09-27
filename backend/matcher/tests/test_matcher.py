"""Tests for match_deeplinks().

Two groups of tests here, and the second group is the important one.

The first checks the labelled set still scores as measured. The second checks the
things that would break M3's integration: malformed input, mutated return values, URL
leakage, and the specific catalog traps where a plausible-looking entry is the wrong
answer.
"""

from __future__ import annotations

import pytest

from backend.matcher.catalog import contains_url
from backend.matcher.evaluate import check_adversarial, evaluate
from backend.matcher.labels import ADVERSARIAL, LABELS, Verdict, labelled_groups
from backend.matcher.matcher import (
    DEFAULT_THRESHOLDS,
    DeeplinkMatcher,
    MatchContext,
    match_deeplinks,
)

# Measured by backend.matcher.evaluate at DEFAULT_THRESHOLDS. If the model, index,
# catalog or gate changes, re-run it and update these deliberately.
EXPECTED_PRECISION = 1.0
EXPECTED_RECALL = 0.8
EXPECTED_ABSTENTION = 1.0


@pytest.fixture(scope="module")
def matcher():
    return DeeplinkMatcher()


# --------------------------------------------------------------- labelled set -----


def test_measured_scores_have_not_regressed(matcher):
    report = evaluate(matcher, DEFAULT_THRESHOLDS)
    assert report.precision == EXPECTED_PRECISION, (
        f"precision regressed to {report.precision:.3f}; "
        f"false positives: {[o.label_key for o in report.outcomes if not o.passed]}"
    )
    assert report.recall >= EXPECTED_RECALL
    assert report.abstention_accuracy == EXPECTED_ABSTENTION
    assert report.false_positive == 0


def test_every_no_match_label_abstains(matcher):
    """30 cases where the catalog genuinely has no entry.

    A single false positive here is a wrong deeplink in a real response, so this is
    asserted case by case rather than as an aggregate.
    """
    matcher.thresholds = DEFAULT_THRESHOLDS
    failures = []
    for label, group in labelled_groups():
        if label.verdict is not Verdict.NONE:
            continue
        got = matcher.match(group.text, MatchContext(heading=group.heading))
        if got:
            failures.append((group.heading, [c.catalog_id for c in got]))
    assert failures == [], f"should have abstained: {failures}"


def test_expected_matches_are_found_with_correct_polarity(matcher):
    """A found match must also have the right direction.

    Returning DL-0126 (Enable) where the step says "turn off this feature" is a wrong
    deeplink even though the setting is right.
    """
    matcher.thresholds = DEFAULT_THRESHOLDS
    checked = 0
    for label, group in labelled_groups():
        if label.verdict is not Verdict.MATCH:
            continue
        got = matcher.match(group.text, MatchContext(heading=group.heading))
        ids = [c.catalog_id for c in got]
        if not set(ids) & label.expected_ids:
            continue  # a known miss, covered by the recall assertion above
        checked += 1
        if label.expected_polarity is not None:
            for candidate in got:
                if candidate.catalog_id in label.expected_ids:
                    assert candidate.polarity == label.expected_polarity, (
                        f"{group.heading}: {candidate.catalog_id} has polarity "
                        f"{candidate.polarity:+d}, expected {label.expected_polarity:+d}"
                    )
    assert checked >= 4, "expected at least 4 labelled matches to be found"


def test_touch_sensitivity_returns_disable_not_enable(matcher):
    """The single clearest polarity case, pinned explicitly.

    Both halves share an identical qna_description, so only the message verb separates
    them. The SIIS text says to turn the feature off.
    """
    matcher.thresholds = DEFAULT_THRESHOLDS
    label, group = next(
        (l, g) for l, g in labelled_groups() if l.heading == "5. Touch Sensitivity Setting"
    )
    ids = [c.catalog_id for c in matcher.match(group.text, MatchContext(heading=group.heading))]
    assert "DL-0125" in ids
    assert "DL-0126" not in ids, "returned the Enable twin for a 'turn off' instruction"


def test_factory_reset_does_not_match_view_reset_options(matcher):
    """The most dangerous trap in the catalog.

    DL-0022 reads "View Reset Options" and embeds at 0.787 against a factory-reset
    step, but its qna is "automatically resets the device after too many failed unlock
    attempts". There is no factory-reset entry, so the answer must be nothing.
    """
    matcher.thresholds = DEFAULT_THRESHOLDS
    label, group = next(
        (l, g) for l, g in labelled_groups() if l.heading == "6. Perform a Factory Data Reset"
    )
    got = matcher.match(group.text, MatchContext(heading=group.heading))
    assert got == [], f"matched {[c.catalog_id for c in got]} on a step with no valid entry"


def test_safe_mode_does_not_match_touch_entries(matcher):
    """Scored 0.704 semantically and 0.279 lexically against unrelated touch entries,
    purely from sitting inside a touch-heavy document."""
    matcher.thresholds = DEFAULT_THRESHOLDS
    label, group = next((l, g) for l, g in labelled_groups() if l.heading == "7. Safe Mode")
    assert matcher.match(group.text, MatchContext(heading=group.heading)) == []


# ------------------------------------------------------------------ robustness -----


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "\t\n", None, 0, 12345, [], {}, object()],
)
def test_malformed_step_text_returns_empty_without_raising(bad):
    """M3 will eventually pass whatever the LLM produced. None and non-strings
    included -- this must degrade to an abstention, never an exception."""
    assert match_deeplinks(bad) == []  # type: ignore[arg-type]


def test_very_long_text_does_not_raise():
    assert isinstance(match_deeplinks("Navigate to Settings and tap Display. " * 500), list)


def test_non_ascii_text_does_not_raise():
    """row_11 of siis_responses.json contains a UTF-8 em dash."""
    assert isinstance(
        match_deeplinks("My Galaxy Flip 6 screen is half black—one side is dark."), list
    )


def test_all_adversarial_inputs_pass(matcher):
    matcher.thresholds = DEFAULT_THRESHOLDS
    failures = [o for o in check_adversarial(matcher) if not o.passed]
    assert failures == [], f"adversarial failures: {[(o.label_key, o.detail) for o in failures]}"


def test_ctx_is_optional_and_none_is_accepted():
    """The agreed signature allows a bare call."""
    assert isinstance(match_deeplinks("Turn on touch sensitivity"), list)
    assert isinstance(match_deeplinks("Turn on touch sensitivity", None), list)


def test_polarity_hint_overrides_text_inference(matcher):
    matcher.thresholds = DEFAULT_THRESHOLDS
    text = "Touch sensitivity Setting. Adjust the touch sensitivity of the display."
    enable = matcher.match(text, MatchContext(polarity_hint=1))
    disable = matcher.match(text, MatchContext(polarity_hint=-1))
    assert all(c.polarity in (0, 1) for c in enable)
    assert all(c.polarity in (0, -1) for c in disable)


# ------------------------------------------------------------------- contract -----


def test_returned_entry_is_the_raw_catalog_entry(matcher):
    """M2's to_deeplink_pair() owns the projection into the response Deeplink shape.

    If this module started reshaping entries, that rule would live in two places and
    they would drift.
    """
    matcher.thresholds = DEFAULT_THRESHOLDS
    by_id = {entry.id: entry.raw for entry in matcher.entries}
    got = matcher.match("Turn off Touch sensitivity in Display settings")
    assert got, "expected at least one candidate for this obvious step"
    for candidate in got:
        assert candidate.entry == by_id[candidate.catalog_id]
        assert set(candidate.entry) == {
            "id",
            "deeplink",
            "description",
            "message",
            "originalType",
            "control_type",
            "qna_description",
            "validation",
        }


def test_no_returned_text_contains_a_url(matcher):
    """G5 is a hard gate: one URL anywhere zeroes the entire automated score."""
    matcher.thresholds = DEFAULT_THRESHOLDS
    for label, group in labelled_groups():
        for candidate in matcher.match(group.text, MatchContext(heading=group.heading)):
            for value in candidate.entry.values():
                if isinstance(value, str):
                    assert not contains_url(value), (
                        f"{candidate.catalog_id} carries URL-shaped text: {value!r}"
                    )


def test_candidates_are_ordered_best_first(matcher):
    matcher.thresholds = DEFAULT_THRESHOLDS
    got = matcher.match("Turn off Touch sensitivity in Display settings")
    scores = [candidate.score for candidate in got]
    assert scores == sorted(scores, reverse=True)


def test_candidate_count_is_capped(matcher):
    matcher.thresholds = DEFAULT_THRESHOLDS
    for label, group in labelled_groups():
        got = matcher.match(group.text, MatchContext(heading=group.heading))
        assert len(got) <= DEFAULT_THRESHOLDS.max_candidates


def test_why_and_signals_are_populated(matcher):
    """M3 logs `why`. An empty diagnostic string is useless at 2am."""
    matcher.thresholds = DEFAULT_THRESHOLDS
    got = matcher.match("Turn off Touch sensitivity in Display settings")
    assert got
    for candidate in got:
        assert "semantic=" in candidate.why and "grounding=" in candidate.why
        assert set(candidate.signals) == {"semantic", "lexical", "grounding"}
        assert 0.0 <= candidate.signals["semantic"] <= 1.0


def test_siis_title_and_query_do_not_pollute_the_match_text():
    """Deliberate design choice, pinned.

    The complaint describes the symptom, not the action. Mixing it into the match text
    pulls results toward whatever the document is broadly about -- the mechanism behind
    the Safe Mode false positive.
    """
    ctx = MatchContext(
        heading="7. Safe Mode",
        siis_title="Touchscreen issues on a Galaxy phone or tablet",
        query="My Galaxy S22 screen inputs are delayed and touch is laggy",
    )
    enriched = ctx.enrich("Restart the device in Safe mode to check for a bad app.")
    assert "Touchscreen issues" not in enriched
    assert "S22" not in enriched
    assert "7. Safe Mode" in enriched


def test_labelled_set_shape_is_what_was_reported():
    counts = {v.value: sum(1 for l in LABELS if l.verdict is v) for v in Verdict}
    assert counts == {"match": 5, "none": 30, "weak": 3}
    assert len(ADVERSARIAL) == 9


# ------------------------------------------------------------- query memo -----


def test_memoized_matcher_returns_identical_results():
    """``memoize_queries=True`` must be a pure speed-up, never a behaviour change.

    It exists for evaluate.py's threshold sweep, which re-embeds the same ~47 texts once
    per threshold combination. Caching those cut the sweep from over ten minutes to ~2,
    and the selected thresholds and scores came out identical -- but a memo that quietly
    altered a score would invalidate every measured number in this module, so it is
    checked rather than assumed.
    """
    plain = DeeplinkMatcher(DEFAULT_THRESHOLDS)
    memoized = DeeplinkMatcher(DEFAULT_THRESHOLDS, memoize_queries=True)

    texts = [
        "5. Touch Sensitivity Setting. To turn off this feature, navigate to Settings, "
        "tap Display, and then tap the switch next to Touch sensitivity.",
        "7. Safe Mode. Restart the device in Safe mode to check for a third-party app.",
        "Use Multi window. From the screen's right side, swipe left to open the Edge panel.",
        "",
        "   ",
    ]
    for text in texts:
        expected = [(c.catalog_id, c.score) for c in plain.match(text)]
        # run twice: the second call is the one served from the memo
        first = [(c.catalog_id, c.score) for c in memoized.match(text)]
        second = [(c.catalog_id, c.score) for c in memoized.match(text)]
        assert first == expected, f"memo changed the result for {text[:40]!r}"
        assert second == expected, f"memo hit changed the result for {text[:40]!r}"


def test_memo_is_off_by_default():
    """Production must not grow a second cache. app/cache.py already handles repeat
    queries at the response level."""
    assert DeeplinkMatcher(DEFAULT_THRESHOLDS)._query_memo is None
    assert DeeplinkMatcher(DEFAULT_THRESHOLDS, memoize_queries=True)._query_memo == {}


def test_memo_actually_avoids_recomputation(monkeypatch):
    """Guards against the memo being present but never consulted."""
    import backend.matcher.matcher as module

    memoized = DeeplinkMatcher(DEFAULT_THRESHOLDS, memoize_queries=True)
    text = "Adjust the screen zoom in Display settings."
    memoized.match(text)

    calls: list[int] = []
    real = module.embed_queries

    def counting(texts, *args, **kwargs):
        calls.append(1)
        return real(texts, *args, **kwargs)

    monkeypatch.setattr(module, "embed_queries", counting)
    memoized.match(text)
    assert calls == [], "the second call should not embed again"
