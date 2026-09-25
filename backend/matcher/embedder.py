"""Embedding backend for the matcher.

Uses ``fastembed`` with ONNX runtime rather than ``sentence-transformers`` with torch.
The reason is deployment, not preference: A3 scores cold-start p95 at 8 seconds on
free hosting, and a torch image is 2-3 GB with a 4-8 second import. This model is
67 MB on onnxruntime, so the image is ~300-500 MB and startup is ~1-2 seconds.

Two operational notes for M2's Dockerfile:

* ``MODEL_NAME`` is pinned. Do not float it -- changing the model invalidates the
  committed index and silently shifts every score.
* fastembed defaults its model cache to the system temp directory, which can be
  cleaned between runs and would turn a cold start into a fresh 30-second download.
  ``CACHE_DIR`` pins it inside the repo instead. The download must happen at image
  **build** time, not on the first request.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

#: Pinned. 384 dimensions, 67 MB, strong English retrieval quality.
MODEL_NAME = "BAAI/bge-small-en-v1.5"

#: Dimensionality of MODEL_NAME. Asserted at load so a model swap cannot pass silently.
EMBED_DIM = 384

#: Kept out of the system temp dir so a temp sweep cannot trigger a re-download, and
#: kept inside this module so it is covered by backend/matcher/.gitignore rather than
#: needing an entry in the shared root .gitignore.
CACHE_DIR = Path(
    os.environ.get("BIXEL_MODEL_CACHE", Path(__file__).resolve().parent / ".model_cache")
)

_model = None


def get_model():
    """Return the embedding model, loading it once per process.

    Loaded lazily so importing this module stays cheap, but callers in a server must
    warm it at startup -- never on the first request.
    """
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _model = TextEmbedding(MODEL_NAME, cache_dir=str(CACHE_DIR))
    return _model


def _normalise(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise rows so a dot product is cosine similarity.

    Zero-length rows would produce NaN, so they are clamped. A zero vector should be
    impossible given ``build_blob`` never returns empty text, but a NaN leaking into
    the scores would silently poison every comparison.
    """
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def embed_documents(texts: Sequence[str]) -> np.ndarray:
    """Embed catalog blobs. Returns an L2-normalised (n, EMBED_DIM) float32 array."""
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    vectors = np.asarray(list(get_model().embed(list(texts))), dtype=np.float32)
    _assert_shape(vectors, len(texts))
    return _normalise(vectors)


#: BGE v1.5's documented retrieval instruction, applied to the query side only.
#:
#: Measured, not assumed. fastembed's ``query_embed`` returns vectors *identical* to
#: ``embed`` for this model -- it does not apply the prefix -- so we apply it here.
#: On the probe set this widened the gap between the weakest true positive and the
#: strongest false positive from +0.0033 to +0.0120, moving all four true positives
#: up and all three false positives down. Every probe agreed on the direction.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def embed_queries(texts: Sequence[str], use_prefix: bool = True) -> np.ndarray:
    """Embed step text for retrieval against the catalog index.

    ``use_prefix`` is exposed so ``evaluate.py`` can measure the prefix's effect on
    the labelled set rather than taking the probe-set result on faith.
    """
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    prepared = [QUERY_PREFIX + text for text in texts] if use_prefix else list(texts)
    vectors = np.asarray(list(get_model().embed(prepared)), dtype=np.float32)
    _assert_shape(vectors, len(texts))
    return _normalise(vectors)


def _assert_shape(vectors: np.ndarray, expected_rows: int) -> None:
    if vectors.shape != (expected_rows, EMBED_DIM):
        raise RuntimeError(
            f"{MODEL_NAME} produced {vectors.shape}, expected ({expected_rows}, {EMBED_DIM}). "
            "If the model was changed, rebuild the index and re-tune the thresholds."
        )


def cosine_scores(query_vector: np.ndarray, document_matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of one normalised query against normalised documents.

    Brute force on purpose. 578 x 384 is tens of microseconds, so FAISS would add a
    dependency and an index-drift failure mode for no measurable gain.
    """
    if document_matrix.size == 0:
        return np.zeros((0,), dtype=np.float32)
    return document_matrix @ query_vector.astype(np.float32)


def iter_batched(items: Iterable[str], size: int = 128):
    """Yield lists of at most ``size`` items."""
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
