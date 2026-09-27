"""Regression tests for two issues found reviewing feat/cache-variations-wiring.

1. The startup warm-up could take /health down with it. That is gate G2, whose failure
   zeroes the entire automated score and skips every live check, so a failed optimisation
   must never stop the service from serving.
2. The cache never evicted anything, so it grew without bound for the life of the process.

Both are cheap to get wrong again, hence tests rather than just fixes.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.cache import MAX_BUCKETS, MAX_ENTRIES_PER_BUCKET, Cache
from app.validator import validate

THIN_REQUEST = {
    "query": "my screen is black",
    "siis_response": {
        "title": "Blank or black display",
        "content": "## Step 2: Force a Restart\nPress and hold the Power button.",
    },
}


# ------------------------------------------------------- G2: startup resilience -----


def test_health_still_answers_when_the_model_warm_up_fails(monkeypatch):
    """The regression this file exists for.

    Before the fix, an exception from get_matcher() propagated out of the lifespan and the
    app could not start at all -- TestClient could not even enter its context, so /health
    was unreachable. A missing model cache or a stale index in the image are both realistic
    causes, and neither should be able to fail G2.
    """

    def boom():
        raise RuntimeError("model download did not complete in the image")

    monkeypatch.setattr(main, "get_matcher", boom)

    with TestClient(main.app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_troubleshoot_still_answers_when_the_model_warm_up_fails(monkeypatch):
    """Warming is an optimisation. Without it the first request pays ~1s of ONNX
    initialisation, which is a far better outcome than a dead endpoint."""

    def boom():
        raise RuntimeError("stale committed index")

    monkeypatch.setattr(main, "get_matcher", boom)

    with TestClient(main.app) as client:
        response = client.post("/v1/troubleshoot", json=THIN_REQUEST)
        assert response.status_code == 200
        payload = response.json()
        assert payload["contexts"], "contexts is never empty"
        assert validate(payload)["ok"] is True


def test_the_warm_up_is_actually_attempted(monkeypatch):
    """The guard must not have turned the warm-up into a no-op.

    Without this, someone could "fix" a flaky warm-up by deleting the call and both tests
    above would still pass while every first request silently paid the ONNX cost.
    """
    calls: list[str] = []

    class Recorder:
        @staticmethod
        def match(text):
            calls.append(text)
            return []

    monkeypatch.setattr(main, "get_matcher", lambda: Recorder)

    with TestClient(main.app) as client:
        client.get("/health")

    assert len(calls) == 1, "the lifespan should warm the model exactly once"


# --------------------------------------------------------- cache is bounded -----


def _siis(n: int) -> dict:
    return {"title": f"doc {n}", "content": f"## Section\nStep number {n}."}


def test_a_bucket_stops_growing_at_the_cap():
    """One document, many distinct queries. Before the fix this list grew forever."""
    cache = Cache(threshold=0.999, max_entries_per_bucket=5)
    siis = _siis(1)
    for i in range(40):
        cache.get_or_compute(f"completely distinct query number {i} about topic {i}", siis, lambda: i)
    assert len(next(iter(cache._buckets.values()))) == 5


def test_the_number_of_buckets_stops_growing_at_the_cap():
    """Many distinct documents. Each one used to add a bucket that was never released."""
    cache = Cache(max_buckets=4)
    for i in range(25):
        cache.get_or_compute("my screen is black", _siis(i), lambda: i)
    assert len(cache._buckets) == 4


def test_eviction_is_oldest_first():
    cache = Cache(max_buckets=2)
    for i in range(3):
        cache.get_or_compute("my screen is black", _siis(i), lambda: i)
    # doc 0 was created first and should be the one dropped
    assert len(cache._buckets) == 2


def test_the_shipped_caps_are_far_above_realistic_use():
    """These bounds must not change any measured behaviour.

    The kit yields 11 buckets, and 20 queries x ~10 paraphrases is ~10 entries per bucket.
    If someone lowers these to near real usage, the cache starts evicting entries the A3
    measurements assume are present.
    """
    assert MAX_ENTRIES_PER_BUCKET >= 32, "would start evicting real paraphrase traffic"
    assert MAX_BUCKETS >= 64, "the kit alone needs 11 buckets"


def test_eviction_does_not_break_hits_for_surviving_entries():
    """A cap is only safe if what remains still works."""
    cache = Cache(max_entries_per_bucket=3)
    siis = _siis(1)
    query = "my Galaxy S22 screen is completely black and will not turn on"
    for i in range(5):
        cache.get_or_compute(f"unrelated filler question {i} regarding topic {i}", siis, lambda: i)
    cache.get_or_compute(query, siis, lambda: "kept")
    before = cache.hits
    assert cache.get_or_compute(query, siis, lambda: "recomputed") == "kept"
    assert cache.hits == before + 1


def test_counters_are_unaffected_by_eviction():
    """Every call here must be a miss, so the count is unambiguous.

    ``threshold=0.999`` forces that. An earlier version of this test used templated queries
    ("distinct query 0 about subject 0", ...) at the default 0.85 and five of six correctly
    hit -- the strings really are near-identical, so that was the cache being right and the
    test being wrong.
    """
    cache = Cache(threshold=0.999, max_entries_per_bucket=2)
    siis = _siis(1)
    for i in range(6):
        cache.get_or_compute(f"distinct query {i} about subject {i}", siis, lambda: i)
    assert cache.misses == 6
    assert cache.hits == 0
    assert len(next(iter(cache._buckets.values()))) == 2, "evicted down to the cap"


def test_embeddings_stored_are_normalised_vectors():
    """Guards the maths the threshold depends on: cosine only equals a dot product when
    both vectors are unit length."""
    cache = Cache()
    cache.get_or_compute("my screen is black", _siis(1), lambda: "x")
    vector = next(iter(cache._buckets.values()))[0][0]
    assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-4)
