"""Tests for the embedder and the committed catalog index.

The index is a committed artefact, so the thing worth testing is that it cannot drift
out of agreement with the catalog or the model without failing loudly. A stale index
produces plausible-looking wrong scores, which is the worst failure mode this module
has.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.matcher.build_index import catalog_checksum, load_index
from backend.matcher.catalog import load_catalog
from backend.matcher.embedder import (
    EMBED_DIM,
    MODEL_NAME,
    cosine_scores,
    embed_documents,
    embed_queries,
    iter_batched,
)


@pytest.fixture(scope="module")
def index():
    return load_index()


@pytest.fixture(scope="module")
def entries():
    return load_catalog()


def test_index_matches_catalog_length(index, entries):
    vectors, ids, manifest = index
    assert vectors.shape == (len(entries), EMBED_DIM)
    assert len(ids) == len(entries)
    assert manifest["count"] == len(entries)


def test_index_ids_align_positionally_with_catalog(index, entries):
    """Scores come back by row position, so a misalignment silently returns wrong entries."""
    _, ids, _ = index
    assert ids == [entry.id for entry in entries]


def test_index_is_pinned_to_the_expected_model(index):
    _, _, manifest = index
    assert manifest["model"] == MODEL_NAME
    assert manifest["dim"] == EMBED_DIM


def test_index_records_the_catalog_checksum(index):
    _, _, manifest = index
    assert manifest["catalog_sha256"] == catalog_checksum()


def test_index_vectors_are_normalised(index):
    vectors, _, _ = index
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4)


def test_index_has_no_nan_or_inf(index):
    vectors, _, _ = index
    assert np.isfinite(vectors).all()


def test_no_two_entries_share_an_identical_vector_unless_text_is_identical(index, entries):
    """143 duplicate qna_description values exist, so identical vectors are expected --
    but only where the blob text is genuinely identical."""
    vectors, ids, _ = index
    by_blob: dict[str, list[int]] = {}
    for position, entry in enumerate(entries):
        by_blob.setdefault(entry.blob, []).append(position)
    for blob, positions in by_blob.items():
        if len(positions) > 1:
            first = vectors[positions[0]]
            for other in positions[1:]:
                assert np.allclose(first, vectors[other], atol=1e-5), (
                    f"identical blob text produced different vectors: {blob[:60]!r}"
                )


def test_embed_documents_is_deterministic():
    text = ["Opens the touch sensitivity settings page in device Settings on the device."]
    first = embed_documents(text)
    second = embed_documents(text)
    assert np.allclose(first, second, atol=1e-6)


def test_embed_documents_matches_the_committed_index(index, entries):
    """Re-embedding a handful of blobs must reproduce the committed vectors.

    This is the check that catches "someone rebuilt with a different model" and
    "the npz was written from a different run".
    """
    vectors, _, _ = index
    sample = [0, 1, 125, 126, 300, 542, 577]
    fresh = embed_documents([entries[i].blob for i in sample])
    for row, position in enumerate(sample):
        assert np.allclose(fresh[row], vectors[position], atol=1e-4), (
            f"entry {entries[position].id} does not reproduce its committed vector"
        )


def test_query_prefix_actually_changes_the_vector():
    """Guards a real trap found while building this.

    fastembed's ``query_embed`` returns vectors identical to ``embed`` for this model,
    so the BGE retrieval prefix is applied by us in ``embed_queries``. If someone
    "simplifies" that back to a bare ``embed`` call, retrieval quality drops silently
    and the tuned thresholds stop being valid. This test fails if that happens.
    """
    text = ["turn on touch sensitivity"]
    with_prefix = embed_queries(text, use_prefix=True)
    without_prefix = embed_queries(text, use_prefix=False)
    assert not np.allclose(with_prefix, without_prefix, atol=1e-3)
    # and the no-prefix path is the plain document embedding
    assert np.allclose(without_prefix, embed_documents(text), atol=1e-6)


def test_fastembed_query_embed_is_not_asymmetric():
    """Documents the upstream behaviour this module works around.

    If a future fastembed version starts applying the prefix inside ``query_embed``,
    this test fails and tells us to stop double-prefixing.
    """
    from backend.matcher.embedder import get_model

    text = ["turn on touch sensitivity"]
    native_query = np.asarray(list(get_model().query_embed(text)), dtype=np.float32)
    native_doc = np.asarray(list(get_model().embed(text)), dtype=np.float32)
    assert np.allclose(native_query, native_doc, atol=1e-6), (
        "fastembed now differentiates query_embed from embed -- remove our manual prefix"
    )


def test_embedding_empty_input_returns_empty_matrix():
    assert embed_documents([]).shape == (0, EMBED_DIM)
    assert embed_queries([]).shape == (0, EMBED_DIM)


def test_cosine_scores_shape_and_range(index):
    vectors, _, _ = index
    query = embed_queries(["turn on touch sensitivity"])[0]
    scores = cosine_scores(query, vectors)
    assert scores.shape == (vectors.shape[0],)
    assert np.isfinite(scores).all()
    assert scores.max() <= 1.0001 and scores.min() >= -1.0001


def test_cosine_scores_on_empty_matrix_is_empty():
    query = np.zeros(EMBED_DIM, dtype=np.float32)
    assert cosine_scores(query, np.zeros((0, EMBED_DIM), dtype=np.float32)).shape == (0,)


def test_iter_batched():
    assert list(iter_batched([], 3)) == []
    assert list(iter_batched(["a"], 3)) == [["a"]]
    assert list(iter_batched(list("abcde"), 2)) == [["a", "b"], ["c", "d"], ["e"]]


def test_warm_model_load_is_fast_enough_for_cold_start():
    """A3 budgets 8s for cold-start p95. Model init with a warm cache must be a small
    fraction of that, since the server also has to import and build its lexical index.

    Not a benchmark -- a regression guard against accidentally switching to a backend
    that takes seconds to initialise.
    """
    import time

    from backend.matcher import embedder

    embedder._model = None  # force a real load from the on-disk cache
    started = time.perf_counter()
    embedder.get_model()
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"warm model load took {elapsed:.1f}s, too slow for an 8s cold start"
