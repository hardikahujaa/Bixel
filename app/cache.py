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

SIMILARITY_THRESHOLD is measured, not assumed, and was revised up once against
real data that broke the first number picked. The first pass probed ten
invented pairs and picked 0.72. Checking that against the actual kit
(student_kit/siis_responses.json: 11 rows share a document with at least one
other row, e.g. six all route to "Blank or black display on a Samsung phone
or tablet") found real collisions up to 0.844 -- e.g. row_2 "screen turns
completely blank or white" vs row_13 "screen stays blank" scored 0.824. Those
are two of the kit's 20 *distinct* graded rows, not paraphrases of each
other, and merging their answers is exactly what docs/KIT_NOTES.md section 5
warns against: "the same document must yield different goals and titles
depending on the query, or six of our twenty answers will be identical."

    same document, different graded kit row (must MISS):  0.572-0.844
    genuine paraphrases of one query, invented pairs:      0.663-0.785
    near-identical rewording ("screen black" / "display black" etc.): 0.895-0.904
    distinct topics entirely:                              0.469-0.608

0.90 sits above every real kit-row collision measured (max 0.844) and every
invented distinct-topic pair, catching near-identical repeats and reworded
duplicates reliably. It also means most of the invented "genuine paraphrase"
pairs above (0.663-0.785) will currently MISS -- a known, honest gap, not
a silent one: at this threshold the cache is proven safe against the real 20
rows but not yet proven to hit on genuinely loose paraphrasing. Closing that
gap needs either a second signal (the way the matcher combines semantic,
lexical and grounding rather than trusting one score) or per-query intent
normalisation, tuned against M4's real paraphrase set on Day 3
(docs/PLAN.md Day 3) -- the same way the matcher's own threshold was tuned
against its hand-labelled set rather than picked once and left alone.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

import numpy as np

from backend.matcher.embedder import cosine_scores, embed_queries

SIMILARITY_THRESHOLD = 0.90


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
