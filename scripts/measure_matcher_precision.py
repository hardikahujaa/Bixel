"""Measures match_deeplinks() against app/matcher_labels.json (Claude.md
Day 1, M1). Prints the rejected cosine-only baseline (for the record: no
threshold separates its positive/negative scores) alongside the shipped
phrase-gate matcher's real precision/recall, run end to end against
app.matcher.match_deeplinks() itself.

Run from the repo root: python -m scripts.measure_matcher_precision
"""
import json
from pathlib import Path

from app.matcher import _get_index, match_deeplinks

LABELS_PATH = Path(__file__).resolve().parent.parent / "app" / "matcher_labels.json"
THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]


def load_labels() -> list[dict]:
    with open(LABELS_PATH, encoding="utf-8") as f:
        return json.load(f)["labels"]


def eval_cosine(labels: list[dict], threshold: float, top_k: int) -> dict:
    index = _get_index()
    tp = fp = tn = fn = 0
    for label in labels:
        acceptable = set(label["acceptable_catalog_ids"])
        candidates = [entry["id"] for entry, score in index.search(label["step_text"], top_k=top_k) if score >= threshold]
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
    return {"threshold": threshold, "tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall}


def eval_shipped_matcher(labels: list[dict]) -> dict:
    """Exercises the real match_deeplinks() from app/matcher.py end to end
    (not a reimplementation) -- this is the number that ships."""
    tp = fp = tn = fn = 0
    rows = []
    for label in labels:
        acceptable = set(label["acceptable_catalog_ids"])
        candidates = {r["catalog_id"] for r in match_deeplinks(label["step_text"])}
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
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall, "rows": rows}


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
            print(f"{r['threshold']:>5.2f} {r['tp']:>3} {r['fp']:>3} {r['tn']:>3} {r['fn']:>3} {r['precision']:>10.2f} {r['recall']:>7.2f}")
        print()

    print("--- shipped matcher: app.matcher.match_deeplinks() end to end ---")
    r = eval_shipped_matcher(labels)
    print(f"tp={r['tp']} fp={r['fp']} tn={r['tn']} fn={r['fn']}  precision={r['precision']:.2f}  recall={r['recall']:.2f}\n")
    for flag, candidates, acceptable, text in r["rows"]:
        if flag != "OK":
            print(f"  [{flag}] candidates={sorted(candidates)} acceptable={sorted(acceptable)} | {text[:80]}")


if __name__ == "__main__":
    main()
