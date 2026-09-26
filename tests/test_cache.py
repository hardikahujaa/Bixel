"""Tests for app/cache.py -- Cache.get_or_compute (docs/PLAN.md, Day 1 interface
list). Uses the real embedding backend (backend/matcher/embedder.py) rather than a
fake: it is already loaded for the matcher's own tests, is what production
actually runs against, and faking it here would only test the mock.

The query pairs below are not arbitrary -- they are the exact strings measured
against the real model while picking SIMILARITY_THRESHOLD (see app/cache.py's
module docstring and scripts/measure_cache_speed.py), so these assertions are
checked against reality rather than against an assumption.
"""
import itertools
import json
from pathlib import Path

import numpy as np

from app.cache import SIMILARITY_THRESHOLD, Cache, _content_key
from backend.matcher.embedder import embed_queries

_SIIS = {"title": "Blank display", "content": "Press and hold the Power button."}
_KIT_ROWS_PATH = Path(__file__).resolve().parent.parent / "student_kit" / "siis_responses.json"


def test_repeat_query_is_a_cache_hit_and_computes_only_once():
    cache = Cache()
    calls = []

    def compute():
        calls.append(1)
        return {"answer": len(calls)}

    first = cache.get_or_compute("my screen is black", _SIIS, compute)
    second = cache.get_or_compute("my screen is black", _SIIS, compute)

    assert first == second
    assert len(calls) == 1
    assert cache.misses == 1
    assert cache.hits == 1


def test_a_near_identical_rewording_is_a_hit():
    """Measured at 0.904 cosine -- above SIMILARITY_THRESHOLD."""
    cache = Cache()

    first = cache.get_or_compute("my screen is black", _SIIS, lambda: "first")
    second = cache.get_or_compute("my screen is dark", _SIIS, lambda: "second")

    assert second == "first"
    assert cache.hits == 1
    assert cache.misses == 1


def test_an_unrelated_query_against_the_same_content_is_a_miss():
    """Measured at 0.469 cosine -- well below SIMILARITY_THRESHOLD."""
    cache = Cache()

    cache.get_or_compute(
        "my Galaxy S22 screen is completely black", _SIIS, lambda: "display answer"
    )
    result = cache.get_or_compute(
        "my wifi keeps dropping every few minutes", _SIIS, lambda: "wifi answer"
    )

    assert result == "wifi answer"
    assert cache.misses == 2
    assert cache.hits == 0


def test_two_distinct_kit_rows_sharing_a_document_are_not_conflated():
    """The trap that moved the threshold from 0.72 to 0.90 and then to 0.85:
    row_2 and row_13's exact original_query strings (student_kit/
    siis_responses.json) both route to the same "Blank or black display"
    document and score 0.824 cosine against each other -- high, but they are
    two different graded rows, not paraphrases of one query. Conflating them
    is exactly what docs/KIT_NOTES.md section 5 warns against: identical
    answers across distinct queries."""
    cache = Cache()
    rows = {r["id"]: r for r in json.loads(_KIT_ROWS_PATH.read_text(encoding="utf-8"))["responses"]}
    row_2 = rows["row_2"]["original_query"]
    row_13 = rows["row_13"]["original_query"]

    cache.get_or_compute(row_2, _SIIS, lambda: "row_2 answer")
    result = cache.get_or_compute(row_13, _SIIS, lambda: "row_13 answer")

    assert result == "row_13 answer"
    assert cache.misses == 2


def test_no_real_kit_rows_collide_at_the_shipped_threshold():
    """Regression guard for the Day 3 finding (app/cache.py's module
    docstring, scripts/measure_cache_speed.py): re-measures every pair of
    distinct kit rows that share a document and fails loudly if any pair's
    own queries now clear SIMILARITY_THRESHOLD against each other -- e.g.
    after an embedding model change. If this ever fails, the fix is a higher
    threshold or a second signal, not silencing the test."""
    rows = json.loads(_KIT_ROWS_PATH.read_text(encoding="utf-8"))["responses"]
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(_content_key(row["siis_response"]), []).append(row)

    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        vectors = embed_queries([row["original_query"] for row in bucket])
        for (i, a), (j, b) in itertools.combinations(enumerate(bucket), 2):
            score = float(np.dot(vectors[i], vectors[j]))
            assert score < SIMILARITY_THRESHOLD, (
                f"{a['id']} and {b['id']} collide at {score:.3f} >= {SIMILARITY_THRESHOLD}"
            )


def test_the_same_query_against_different_content_is_a_miss():
    """Keyed on content too -- an identical query string must not leak an
    answer built for a different SIIS document."""
    cache = Cache()
    other_siis = {"title": "Battery draining", "content": "Check background app usage."}

    cache.get_or_compute("my screen is black", _SIIS, lambda: "display answer")
    result = cache.get_or_compute("my screen is black", other_siis, lambda: "battery answer")

    assert result == "battery answer"
    assert cache.misses == 2


def test_starts_with_zero_hits_and_misses():
    cache = Cache()
    assert cache.hits == 0
    assert cache.misses == 0


# --------------------------------------------------------------------- store_if


def test_store_if_false_never_caches_a_miss():
    """A degraded (e.g. fallback) answer withheld via store_if must be
    recomputed on every later call, not served stale forever."""
    cache = Cache()
    calls = []

    def compute():
        calls.append(1)
        return "degraded answer"

    cache.get_or_compute("my screen is black", _SIIS, compute, store_if=lambda _: False)
    cache.get_or_compute("my screen is black", _SIIS, compute, store_if=lambda _: False)

    assert len(calls) == 2
    assert cache.misses == 2
    assert cache.hits == 0


def test_store_if_true_caches_normally():
    cache = Cache()
    calls = []

    def compute():
        calls.append(1)
        return "good answer"

    cache.get_or_compute("my screen is black", _SIIS, compute, store_if=lambda _: True)
    cache.get_or_compute("my screen is black", _SIIS, compute, store_if=lambda _: True)

    assert len(calls) == 1
    assert cache.hits == 1
