"""Measure the matcher against the hand-labelled set and select thresholds.

    <py312> -m backend.matcher.evaluate

Prints a threshold sweep, the selected operating point, and every confusion case with
its raw signals. Thresholds are chosen here from data, not picked by hand -- and if the
best achievable precision at usable recall is poor, that is reported rather than fixed
by lowering the bar to manufacture coverage.

Scoring rules:

* a ``MATCH`` label passes when one of its ``expected_ids`` is among the returned
  candidates. Ambiguity is represented by the list, so demanding an exact top-1 would
  punish the matcher for honestly reporting a polarity pair it cannot split.
* a ``NONE`` label passes when nothing is returned.
* ``WEAK`` labels are reported but excluded from the numbers -- they are genuinely
  arguable and scoring them either way would be dishonest.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

from .labels import ADVERSARIAL, LABELS, Verdict, labelled_groups
from .matcher import DeeplinkMatcher, MatchContext, Thresholds


@dataclass
class Outcome:
    label_key: str
    verdict: str
    passed: bool
    returned: list[str]
    detail: str


@dataclass
class Report:
    thresholds: Thresholds
    true_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0
    false_positive: int = 0
    outcomes: list[Outcome] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.outcomes is None:
            self.outcomes = []

    @property
    def precision(self) -> float:
        predicted = self.true_positive + self.false_positive
        return self.true_positive / predicted if predicted else 0.0

    @property
    def recall(self) -> float:
        actual = self.true_positive + self.false_negative
        return self.true_positive / actual if actual else 0.0

    @property
    def abstention_accuracy(self) -> float:
        total = self.true_negative + self.false_positive
        return self.true_negative / total if total else 0.0

    @property
    def f1(self) -> float:
        if not (self.precision and self.recall):
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)


def evaluate(matcher: DeeplinkMatcher, thresholds: Thresholds) -> Report:
    matcher.thresholds = thresholds
    report = Report(thresholds=thresholds)

    for label, group in labelled_groups():
        ctx = MatchContext(heading=group.heading, siis_title=group.doc_title)
        candidates = matcher.match(group.text, ctx)
        returned = [candidate.catalog_id for candidate in candidates]

        if label.verdict is Verdict.WEAK:
            report.outcomes.append(
                Outcome(label.heading, "weak", True, returned, "not scored")
            )
            continue

        if label.verdict is Verdict.MATCH:
            hit = bool(set(returned) & label.expected_ids)
            if hit:
                report.true_positive += 1
                detail = "ok"
            else:
                report.false_negative += 1
                detail = f"expected one of {sorted(label.expected_ids)}"
                if returned:
                    # Returning the WRONG entry is not merely a miss -- it is a wrong
                    # deeplink, the exact failure this module exists to avoid. Counting
                    # it only as a false negative understates the harm and would let a
                    # gate that confidently returns wrong answers look precise.
                    report.false_positive += 1
                    detail += " and returned a wrong entry instead"
            report.outcomes.append(
                Outcome(label.heading, "match", hit, returned, detail)
            )
        else:
            clean = not returned
            if clean:
                report.true_negative += 1
                detail = "ok"
            else:
                report.false_positive += 1
                detail = "should have abstained"
            report.outcomes.append(
                Outcome(label.heading, "none", clean, returned, detail)
            )

    return report


def check_adversarial(matcher: DeeplinkMatcher) -> list[Outcome]:
    """Adversarial inputs must never raise and never match."""
    results: list[Outcome] = []
    for name, text, reason in ADVERSARIAL:
        try:
            candidates = matcher.match(text)  # type: ignore[arg-type]
            returned = [candidate.catalog_id for candidate in candidates]
            results.append(
                Outcome(name, "adversarial", not returned, returned, reason)
            )
        except Exception as exc:  # noqa: BLE001 - we are testing for exactly this
            results.append(
                Outcome(name, "adversarial", False, [], f"RAISED {type(exc).__name__}: {exc}")
            )
    return results


def sweep(matcher: DeeplinkMatcher) -> list[Report]:
    semantic_grid = [0.62, 0.66, 0.70, 0.72, 0.74, 0.75, 0.76, 0.77, 0.78, 0.79, 0.80]
    grounding_grid = [0.0, 0.34, 0.50, 0.60, 0.67, 0.75, 1.0]
    lexical_grid = [0.0, 0.10, 0.20]

    reports: list[Report] = []
    base = Thresholds()
    for semantic, grounding, lexical in itertools.product(
        semantic_grid, grounding_grid, lexical_grid
    ):
        thresholds = replace(
            base, semantic=semantic, grounding=grounding, lexical=lexical
        )
        reports.append(evaluate(matcher, thresholds))
    return reports


def select(reports: list[Report]) -> Report:
    """Pick the centre of the best-scoring plateau.

    Precision first, then recall -- that ordering matches the cost function, since a
    wrong deeplink is worse than a missing one.

    The third criterion matters more than it looks. Many gates tie on (precision,
    recall) because the thresholds form a flat plateau, and taking the *strictest* tied
    gate silently discarded a true positive scoring 0.745 when the plateau ran from
    0.72 to 0.78. An edge of a plateau is the least robust point on it: on unseen data
    the boundary moves, and a gate sitting on the edge falls off. So among tied gates we
    take the one nearest the middle of the tied semantic range.
    """
    best_score = max(
        (round(r.precision, 4), round(r.recall, 4)) for r in reports
    )
    tied = [
        r
        for r in reports
        if (round(r.precision, 4), round(r.recall, 4)) == best_score
    ]
    semantics = sorted({r.thresholds.semantic for r in tied})
    groundings = sorted({r.thresholds.grounding for r in tied})
    mid_semantic = semantics[len(semantics) // 2]
    mid_grounding = groundings[len(groundings) // 2]
    return min(
        tied,
        key=lambda r: (
            abs(r.thresholds.semantic - mid_semantic),
            abs(r.thresholds.grounding - mid_grounding),
            r.thresholds.lexical,
        ),
    )


def main() -> None:
    # memoize_queries: the sweep calls match() with the same ~47 texts once per
    # threshold combination, and embeddings do not depend on thresholds. Without it
    # this script ran for over ten minutes.
    matcher = DeeplinkMatcher(memoize_queries=True)
    counts = {v.value: sum(1 for l in LABELS if l.verdict is v) for v in Verdict}
    print(f"labelled set: {counts}  adversarial: {len(ADVERSARIAL)}")
    print(f"index: {matcher.manifest['model']} dim={matcher.manifest['dim']} "
          f"count={matcher.manifest['count']}\n")

    reports = sweep(matcher)
    scored = [r for r in reports if r.true_positive + r.false_positive > 0]

    print("=== sweep: gates that return anything at all ===")
    print(f"{'sem':>5} {'grnd':>5} {'lex':>5} | {'TP':>3} {'FN':>3} {'FP':>3} {'TN':>3} "
          f"| {'prec':>6} {'rec':>6} {'f1':>6} {'abst':>6}")
    seen: set[tuple[float, float, float]] = set()
    for report in sorted(
        scored,
        key=lambda r: (-round(r.precision, 4), -round(r.recall, 4)),
    )[:22]:
        t = report.thresholds
        key = (t.semantic, t.grounding, t.lexical)
        if key in seen:
            continue
        seen.add(key)
        print(
            f"{t.semantic:5.2f} {t.grounding:5.2f} {t.lexical:5.2f} | "
            f"{report.true_positive:3d} {report.false_negative:3d} "
            f"{report.false_positive:3d} {report.true_negative:3d} | "
            f"{report.precision:6.3f} {report.recall:6.3f} {report.f1:6.3f} "
            f"{report.abstention_accuracy:6.3f}"
        )

    best = select(reports)
    print(f"\n=== selected: {best.thresholds.describe()} ===")
    print(f"precision {best.precision:.3f}  recall {best.recall:.3f}  "
          f"f1 {best.f1:.3f}  abstention accuracy {best.abstention_accuracy:.3f}")
    print(f"TP={best.true_positive} FN={best.false_negative} "
          f"FP={best.false_positive} TN={best.true_negative}")

    print("\n=== every case that did not pass at the selected gate ===")
    failures = [o for o in best.outcomes if not o.passed]
    if not failures:
        print("  (none)")
    for outcome in failures:
        print(f"  [{outcome.verdict:5}] {outcome.label_key}")
        print(f"          returned={outcome.returned} -- {outcome.detail}")

    print("\n=== weak (arguable, unscored) ===")
    for outcome in best.outcomes:
        if outcome.verdict == "weak":
            print(f"  {outcome.label_key}: returned={outcome.returned}")

    matcher.thresholds = best.thresholds
    print("\n=== adversarial ===")
    for outcome in check_adversarial(matcher):
        status = "ok  " if outcome.passed else "FAIL"
        print(f"  {status} {outcome.label_key:22} returned={outcome.returned}")

    print(f"\nRECOMMENDED Thresholds(semantic={best.thresholds.semantic}, "
          f"lexical={best.thresholds.lexical}, grounding={best.thresholds.grounding})")


if __name__ == "__main__":
    main()
