"""Response cache. Owner: M2 (docs/PLAN.md, "Who owns what").

Cache.get_or_compute(query, siis, fn) -> response

Keyed on an embedding of the query's intent plus a hash of the SIIS content,
so a paraphrase of the same problem hits the same entry rather than needing
an exact string match. Hit/miss counts are exposed because they're scored
under A3 and needed for the Day 3 speed numbers (repeat-query p95 <=300ms,
>=90% hit rate; paraphrase hit rate >=80%, docs/PLAN.md Day 3).

Reuses the matcher's embedding backend (backend/matcher/embedder.py) instead
of loading a second model -- that BGE session is already warmed once at
process start for the matcher, and a cache miss pays for an LLM call anyway,
so there is nothing to gain from a second, lighter embedder here.

SIMILARITY_THRESHOLD went through two revisions, each forced by checking the
previous number against real data instead of trusting the invented pairs
that picked it:

1. An invented ten-pair probe suggested 0.72. Checking that against the
   actual kit (student_kit/siis_responses.json: 11 of the 20 rows share a
   document with at least one other row, e.g. six all route to "Blank or
   black display on a Samsung phone or tablet") found real collisions
   between DIFFERENT graded rows up to 0.844 -- e.g. row_2 and row_13 both
   describe a blank S22 screen but are different complaints, and scored
   0.824 against each other. Conflating two of the kit's 20 distinct graded
   rows is exactly what docs/KIT_NOTES.md section 5 warns against: "the same
   document must yield different goals and titles depending on the query, or
   six of our twenty answers will be identical." That pushed the threshold up
   to 0.90.

2. 0.90 was then checked against real paraphrase data -- variations() run
   live against all 20 kit queries, fixtures/paraphrases.json, swept in
   scripts/measure_cache_speed.py -- and found to only hit 47.5% of genuine
   paraphrases (target: 80%, docs/PLAN.md Day 3). The sweep also showed
   something the first probe's ten invented pairs never could: across the
   *entire* 0.80-0.95 range, no real paraphrase's argmax ever lands on the
   WRONG row in its bucket, even where two different rows' own queries sit
   close together. Only the original-vs-original collision matters here, and
   its measured maximum is 0.844 -- checked twice, once against this venv's
   drifted fastembed/onnxruntime and once against the exact pins in
   requirements.txt, both giving the same 0.844.

    threshold  correct-paraphrase-rate  original-row collisions
        0.800                   99.4%   3
        0.840                   90.1%   1
        0.845                   88.3%   0   <- lowest safe value measured
        0.850                   86.4%   0   <- shipped
        0.860                   80.9%   0
        0.900                   47.5%   0

0.85 is the shipped value: zero collisions in both dependency environments
tested, 86.4% correct-paraphrase rate against the 80% target, and a small
margin (0.006) above the measured 0.844 ceiling. tests/test_cache.py's
test_no_real_kit_rows_collide_at_the_shipped_threshold re-checks that ceiling
against the live kit data on every test run, so a future embedding-model or
catalog change that moves it gets caught immediately rather than silently.
Run `python -m scripts.measure_cache_speed` to reproduce or re-tune this.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

import numpy as np

from backend.matcher.embedder import cosine_scores, embed_queries

SIMILARITY_THRESHOLD = 0.85


def _content_key(siis: dict[str, Any]) -> str:
    """Hash the SIIS payload's meaning, not its exact bytes.

    Keyed on title + content only -- the two fields siis_response ever has
    (docs/KIT_NOTES.md section 5) -- so the same document always hashes the
    same way regardless of what else a caller's dict happens to carry.
    """
    title = (siis.get("title") or "").strip()
    content = (siis.get("content") or "").strip()
    return hashlib.sha256(f"{title}\n{content}".encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, threshold: float = SIMILARITY_THRESHOLD) -> None:
        self.hits = 0
        self.misses = 0
        self._threshold = threshold
        # content hash -> [(query embedding, response), ...], most recent last.
        self._buckets: dict[str, list[tuple[np.ndarray, Any]]] = {}

    def get_or_compute(
        self,
        query: str,
        siis: dict[str, Any],
        fn: Callable[[], Any],
        *,
        store_if: Callable[[Any], bool] | None = None,
    ) -> Any:
        """Return a cached response for an equivalent (query, siis) pair, or
        compute one with ``fn()`` and store it.

        "Equivalent" means: the same SIIS content, and a query whose intent
        embedding is within ``SIMILARITY_THRESHOLD`` cosine similarity of one
        already served for that content. A different SIIS document always
        starts a fresh bucket, even for the identical query string.

        ``store_if`` is optional and additive to the agreed three-argument
        shape (docs/PLAN.md, Day 1 interface list) -- callers that don't need
        it get the original behaviour of always storing a miss. It exists so
        a caller can withhold a degraded answer from the cache: the risk
        table in docs/PLAN.md names Gemini 503s during judging as a real
        risk, and a 503 producing the deterministic fallback (score 0.35)
        must not get served to every later paraphrase of that query forever
        -- it should retry, since the next call might land after Gemini
        recovers. See app/main.py's use of this with an ExtractionTrace.
        """
        bucket = self._buckets.setdefault(_content_key(siis), [])
        query_vector = embed_queries([query])[0]

        if bucket:
            document_matrix = np.stack([vector for vector, _ in bucket])
            scores = cosine_scores(query_vector, document_matrix)
            best_index = int(np.argmax(scores))
            if scores[best_index] >= self._threshold:
                self.hits += 1
                return bucket[best_index][1]

        self.misses += 1
        response = fn()
        if store_if is None or store_if(response):
            bucket.append((query_vector, response))
        return response
