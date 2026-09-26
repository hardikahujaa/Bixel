"""Measures the Day 3 cache speed targets (docs/PLAN.md, Day 3, M2):

    repeat query, p95, <= 300 ms
    cache hit rate, >= 90% on repeats
    paraphrase hit rate, >= 80%, fed by M4's real paraphrases
    cold start, p95, <= 8 s

Run from the repo root: python -m scripts.measure_cache_speed

Needs GEMINI_API_KEY the first time it runs (to seed the 20 kit rows' real
extract() answers and to generate M4's paraphrase file). Every run after
that reuses fixtures/paraphrases.json -- the committed deliverable
docs/PLAN.md, Day 1 asks M4 for ("all 20 queries x 8-10 paraphrases, done
and committed") -- and needs no further Gemini calls for the threshold
sweep, which runs on embeddings alone.

The paraphrase hit rate is measured the way the cache actually decides a
hit: argmax similarity across every original query already seeded in that
paraphrase's content bucket (app/cache.py's get_or_compute), not just
against the paraphrase's own original. 11 of the 20 kit rows share a
document with at least one other row, so a paraphrase that clears the
threshold against the WRONG row in its bucket is not a correct hit -- it is
the exact failure app/cache.py's docstring is built around avoiding, and an
earlier version of this script missed it by scoring each paraphrase only
against its own original.
"""
from __future__ import annotations

import itertools
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.main import _cache, app
from app.variations import variations
from backend.matcher.embedder import embed_queries
from app.cache import _content_key

ROWS_PATH = Path(__file__).resolve().parent.parent / "student_kit" / "siis_responses.json"
PARAPHRASES_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "paraphrases.json"

THRESHOLD_SWEEP = [round(0.80 + 0.005 * i, 3) for i in range(31)]  # 0.80 .. 0.95


def load_rows() -> list[dict]:
    return json.loads(ROWS_PATH.read_text(encoding="utf-8"))["responses"]


def load_or_generate_paraphrases(rows: list[dict]) -> dict[str, list[str]]:
    """M4's Day 1 deliverable (docs/PLAN.md): 8-10 paraphrases per kit query,
    committed to a file. Generated once with the real Gemini client via
    variations(), then reused -- so the threshold sweep below never needs to
    call the API again."""
    if PARAPHRASES_PATH.exists():
        return json.loads(PARAPHRASES_PATH.read_text(encoding="utf-8"))

    out: dict[str, list[str]] = {}
    for row in rows:
        out[row["id"]] = variations(row["original_query"])
    PARAPHRASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAPHRASES_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def measure_repeat_latency_and_hit_rate(rows: list[dict]) -> dict:
    """One miss call to seed each row (real Gemini), then one repeat call.
    Hit rate is repeat-hits / repeat-calls -- the 20 seed calls are misses
    by construction and do not belong in this denominator."""
    client = TestClient(app)
    _cache.hits = 0
    _cache.misses = 0
    _cache._buckets.clear()

    hit_latencies = []
    repeat_hits = 0
    per_row = []
    for row in rows:
        payload = {"query": row["original_query"], "siis_response": row["siis_response"]}
        client.post("/v1/troubleshoot", json=payload)  # seed (a miss, always)
        misses_before = _cache.misses
        t0 = time.perf_counter()
        client.post("/v1/troubleshoot", json=payload)  # repeat
        elapsed = time.perf_counter() - t0
        hit_latencies.append(elapsed)
        was_hit = _cache.misses == misses_before
        repeat_hits += int(was_hit)
        per_row.append((row["id"], elapsed, was_hit))

    return {
        "p95_repeat_latency_s": _p95(hit_latencies),
        "hit_rate": repeat_hits / len(rows),
        "repeat_hits": repeat_hits,
        "repeat_calls": len(rows),
        "seeded_on_model_route": _cache.hits + _cache.misses - len(rows) == 0,
        "per_row": per_row,
    }


def _p95(latencies: list[float]) -> float:
    ordered = sorted(latencies)
    return ordered[int(0.95 * (len(ordered) - 1))]


#: Generic words that appear in the majority of these 20 queries regardless
#: of which specific problem they describe -- dropped so the overlap check
#: below measures whether two texts share the same *specific* complaint
#: (device model, app name, symptom detail), not just the same topic.
_GENERIC = frozenset({
    "a", "an", "the", "on", "in", "of", "for", "to", "and", "or", "your", "you", "is",
    "are", "be", "with", "from", "at", "by", "it", "its", "this", "that", "these", "my",
    "i", "so", "no", "not", "even", "when", "after", "while", "just", "again", "still",
    "samsung", "galaxy", "phone", "tablet", "device", "screen", "display", "black",
    "dark", "blank", "completely", "totally", "won't", "wont", "doesn't", "doesnt",
})
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _distinctive_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _GENERIC and len(t) > 2}


def sweep_paraphrase_hit_rate(rows: list[dict], paraphrases: dict[str, list[str]]) -> dict:
    """For every threshold in THRESHOLD_SWEEP: seed each content bucket with
    ALL of its rows' original queries (matching the harness posting all 20
    kit rows), then classify every paraphrase by the bucket-wide argmax --
    exactly what Cache.get_or_compute does."""
    buckets: dict[str, list[str]] = {}
    for row in rows:
        buckets.setdefault(_content_key(row["siis_response"]), []).append(row["id"])

    all_ids = [row["id"] for row in rows]
    all_queries = {row["id"]: row["original_query"] for row in rows}
    original_vecs = dict(zip(all_ids, embed_queries([all_queries[i] for i in all_ids])))

    # Original-vs-original collisions: two DIFFERENT kit rows in the same
    # bucket whose own queries already clear a given threshold against each
    # other. If this ever happens the harness would serve one row's answer
    # to the other regardless of any paraphrase.
    collisions = []
    for bucket_ids in buckets.values():
        for a, b in itertools.combinations(bucket_ids, 2):
            score = float(np.dot(original_vecs[a], original_vecs[b]))
            collisions.append((a, b, score))
    max_collision = max((c[2] for c in collisions), default=0.0)

    para_ids = [(row_id, i) for row_id in all_ids for i in range(len(paraphrases[row_id]))]
    para_texts = [paraphrases[row_id][i] for row_id, i in para_ids]
    para_vecs = embed_queries(para_texts) if para_texts else np.zeros((0, 384), dtype=np.float32)

    row_to_bucket = {row_id: key for key, ids in buckets.items() for row_id in ids}

    results = {}
    for threshold in THRESHOLD_SWEEP:
        correct = wrong = miss = 0
        for (row_id, _), vec in zip(para_ids, para_vecs):
            bucket_ids = buckets[row_to_bucket[row_id]]
            scores = {other: float(np.dot(vec, original_vecs[other])) for other in bucket_ids}
            best_id, best_score = max(scores.items(), key=lambda kv: kv[1])
            if best_score < threshold:
                miss += 1
            elif best_id == row_id:
                correct += 1
            else:
                wrong += 1
        collisions_at_threshold = sum(1 for *_ids, score in collisions if score >= threshold)
        total = correct + wrong + miss
        results[threshold] = {
            "correct": correct, "wrong": wrong, "miss": miss, "total": total,
            "correct_rate": correct / total if total else float("nan"),
            "original_collisions": collisions_at_threshold,
        }

    return {"per_threshold": results, "max_collision": max_collision, "collisions": collisions}


def measure_cold_start(trials: int = 5) -> dict:
    """Times fresh local processes importing app.main and running the same
    warm-up call app.main's lifespan hook runs at real startup. This is
    local process start, not a Render wake from sleep -- Day 4's keep-alive
    ping is what covers that."""
    code = (
        "import time\n"
        "t0 = time.perf_counter()\n"
        "from app.main import app\n"
        "from backend.matcher.matcher import get_matcher\n"
        "get_matcher().match('warm up the embedding model')\n"
        "print(time.perf_counter() - t0)\n"
    )
    repo_root = Path(__file__).resolve().parent.parent
    samples = []
    for _ in range(trials):
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=repo_root
        )
        samples.append(float(result.stdout.strip()))
    return {"samples": samples, "p95": _p95(samples)}


def main() -> None:
    rows = load_rows()
    paraphrases = load_or_generate_paraphrases(rows)

    print(f"=== repeat-query latency & cache hit rate (real Gemini, {len(rows)} kit rows) ===")
    r = measure_repeat_latency_and_hit_rate(rows)
    print(f"p95 repeat-call latency: {r['p95_repeat_latency_s'] * 1000:.1f} ms (target <= 300 ms)")
    print(
        f"hit rate on repeats: {r['hit_rate'] * 100:.1f}% (target >= 90%) -- "
        f"{r['repeat_hits']}/{r['repeat_calls']}"
    )
    for row_id, elapsed, was_hit in r["per_row"]:
        if not was_hit:
            print(f"  {row_id}: MISS on repeat ({elapsed * 1000:.0f} ms) -- seed call did not take the model route")
    print()

    print(f"=== paraphrase hit rate sweep (real variations() output, {len(rows)} kit rows) ===")
    sweep = sweep_paraphrase_hit_rate(rows, paraphrases)
    print(f"max original-vs-original collision across all same-document row pairs: {sweep['max_collision']:.3f}")
    print(f"{'thr':>6} {'correct':>8} {'wrong':>6} {'miss':>5} {'rate':>7} {'collisions':>11}")
    qualifying = []
    for threshold in THRESHOLD_SWEEP:
        s = sweep["per_threshold"][threshold]
        print(
            f"{threshold:>6.3f} {s['correct']:>8} {s['wrong']:>6} {s['miss']:>5} "
            f"{s['correct_rate'] * 100:>6.1f}% {s['original_collisions']:>11}"
        )
        if s["wrong"] == 0 and s["original_collisions"] == 0 and s["correct_rate"] >= 0.80:
            qualifying.append(threshold)
    print()
    if qualifying:
        chosen = min(qualifying)
        margin = chosen - sweep["max_collision"]
        print(
            f"qualifying thresholds (zero wrong hits, zero collisions, >=80% correct): {qualifying}"
        )
        print(f"lowest qualifying threshold: {chosen} (margin over max collision: {margin:.3f})")
    else:
        print("NO threshold in [0.80, 0.95] gives zero wrong hits, zero collisions and >=80% correct.")
        print("Distinctive-token overlap (stopwords/generic device-topic words removed):")
        for a, b, score in sorted(sweep["collisions"], key=lambda c: -c[2])[:5]:
            qa, qb = next(r["original_query"] for r in rows if r["id"] == a), next(
                r["original_query"] for r in rows if r["id"] == b
            )
            overlap = _distinctive_tokens(qa) & _distinctive_tokens(qb)
            print(f"  colliding pair {a}/{b} (cosine {score:.3f}): shared distinctive tokens = {sorted(overlap)}")
    print()

    print("=== cold start: local process start, warm the embedding model (5 trials) ===")
    cold = measure_cold_start()
    print(f"samples: {[round(s, 2) for s in cold['samples']]}")
    print(f"p95 cold start: {cold['p95']:.2f}s (target <= 8s)")
    print("(this is local process start, not a Render wake from sleep -- Day 4's keep-alive ping covers that)")


if __name__ == "__main__":
    main()
