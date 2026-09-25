"""Measures match_deeplinks() against app/matcher_labels.json (docs/PLAN.md, Day 1, M1).

Prints the rejected cosine-only baseline (for the record: no threshold separates its
positive/negative scores) alongside the shipped matcher's real precision/recall, run end
to end against the matcher that actually ships.

Run from the repo root: python -m scripts.measure_matcher_precision

--------------------------------------------------------------------------------
PROVENANCE. Written by Siddhant alongside his phrase-gate matcher in 4e7f8c8, and
deleted in af8bdd2 when that matcher was replaced by backend/matcher. Restored and
repointed, because the cosine-only rejection it documents is load-bearing evidence:
it independently reached the same conclusion the backend/matcher work did, that no
lexical-similarity threshold separates a real match from a correct no-match on this
catalog.

Two changes were needed to make it run again:
  1. `from app.matcher import _get_index, match_deeplinks` -> that module no longer
     exists. The shipped matcher is now backend.matcher.matcher.match_deeplinks, which
     returns MatchCandidate objects rather than dicts.
  2. His cosine baseline lived in the deleted app.matcher.CatalogIndex.search. It is
     reproduced below, faithfully, including his deliberate double-weighting of
     qna_description -- the point is to reproduce the baseline he rejected, not to
     improve it.

His 29 labels themselves are unmodified; see app/matcher_labels.json.
--------------------------------------------------------------------------------
"""
import json
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from backend.matcher.catalog import load_catalog
from backend.matcher.matcher import match_deeplinks

LABELS_PATH = Path(__file__).resolve().parent.parent / "app" / "matcher_labels.json"
THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]


def load_labels() -> list[dict]:
    with open(LABELS_PATH, encoding="utf-8") as f:
        return json.load(f)["labels"]


class CosineBaseline:
    """The rejected cosine-only matcher, reproduced from the deleted app.matcher.

    qna_description is repeated twice in the entry text on purpose -- that was the
    original weighting, and changing it would mean measuring a different baseline than
    the one that was rejected.
    """

    def __init__(self) -> None:
        self.entries = [entry.raw for entry in load_catalog()]
        texts = [self._entry_text(e) for e in self.entries]
        self.vectorizer = TfidfVectorizer(
            lowercase=True, stop_words="english", ngram_range=(1, 2)
        )
        self.matrix = self.vectorizer.fit_transform(texts)

    @staticmethod
    def _entry_text(entry: dict) -> str:
        qna = entry.get("qna_description") or ""
        message = entry.get("message") or ""
        description = entry.get("description") or ""
        return " ".join(filter(None, [qna, qna, message, description]))

    def search(self, step_text: str, top_k: int = 5) -> list[tuple[dict, float]]:
        query_vec = self.vectorizer.transform([step_text])
        sims = cosine_similarity(query_vec, self.matrix)[0]
        ranked = sims.argsort()[::-1][:top_k]
        return [(self.entries[i], float(sims[i])) for i in ranked]


_baseline: CosineBaseline | None = None


def _get_index() -> CosineBaseline:
    global _baseline
    if _baseline is None:
        _baseline = CosineBaseline()
    return _baseline


def eval_cosine(labels: list[dict], threshold: float, top_k: int) -> dict:
    index = _get_index()
    tp = fp = tn = fn = 0
    for label in labels:
        acceptable = set(label["acceptable_catalog_ids"])
        candidates = [
            entry["id"]
            for entry, score in index.search(label["step_text"], top_k=top_k)
            if score >= threshold
        ]
        if acceptable:
            if acceptable & set(candidates):
                tp += 1
            else:
                fn += 1
        else:
            if candidates:
                fp += 1
            else:
                tn += 1
    predicted_positive = tp + fp
    total_positive = tp + fn
    precision = tp / predicted_positive if predicted_positive else float("nan")
    recall = tp / total_positive if total_positive else float("nan")
    return {
        "threshold": threshold, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision, "recall": recall,
    }


def eval_shipped_matcher(labels: list[dict]) -> dict:
    """Exercises the real backend.matcher.matcher.match_deeplinks() end to end
    (not a reimplementation) -- this is the number that ships."""
    tp = fp = tn = fn = 0
    rows = []
    for label in labels:
        acceptable = set(label["acceptable_catalog_ids"])
        candidates = {c.catalog_id for c in match_deeplinks(label["step_text"])}
        if acceptable:
            if acceptable & candidates:
                tp += 1
                flag = "OK"
            else:
                fn += 1
                flag = "MISS(fn)"
        else:
            if candidates:
                fp += 1
                flag = "MISS(fp)"
            else:
                tn += 1
                flag = "OK"
        rows.append((flag, candidates, acceptable, label["step_text"]))
    predicted_positive = tp + fp
    total_positive = tp + fn
    precision = tp / predicted_positive if predicted_positive else float("nan")
    recall = tp / total_positive if total_positive else float("nan")
    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision, "recall": recall, "rows": rows,
    }


def main() -> None:
    labels = load_labels()
    n_pos = sum(1 for l in labels if l["acceptable_catalog_ids"])
    n_neg = len(labels) - n_pos
    print(f"{len(labels)} labeled examples ({n_pos} expect a match, {n_neg} expect abstain)\n")

    for top_k, label_name in [(1, "cosine-only, top_k=1"), (3, "cosine-only, top_k=3 (recall@3)")]:
        print(f"--- {label_name} ---")
        header = f"{'thr':>5} {'tp':>3} {'fp':>3} {'tn':>3} {'fn':>3} {'precision':>10} {'recall':>7}"
        print(header)
        print("-" * len(header))
        for t in THRESHOLDS:
            r = eval_cosine(labels, t, top_k)
            print(
                f"{r['threshold']:>5.2f} {r['tp']:>3} {r['fp']:>3} {r['tn']:>3} "
                f"{r['fn']:>3} {r['precision']:>10.2f} {r['recall']:>7.2f}"
            )
        print()

    print("--- shipped matcher: backend.matcher.matcher.match_deeplinks() end to end ---")
    r = eval_shipped_matcher(labels)
    print(
        f"tp={r['tp']} fp={r['fp']} tn={r['tn']} fn={r['fn']}  "
        f"precision={r['precision']:.2f}  recall={r['recall']:.2f}\n"
    )
    for flag, candidates, acceptable, text in r["rows"]:
        if flag != "OK":
            print(f"  [{flag}] candidates={sorted(candidates)} acceptable={sorted(acceptable)} | {text[:80]}")


if __name__ == "__main__":
    main()
