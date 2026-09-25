"""Build the catalog index artefact.

Run once, commit the result. Embedding 578 entries takes ~12 seconds, which is
tolerable offline but not on a cold start with an 8-second budget, and recomputing it
per deploy would also make scores drift silently if the model ever changed.

    <py312> -m backend.matcher.build_index

Writes ``backend/matcher/index/catalog_index.npz`` (vectors, ids) and
``catalog_index.json`` (manifest: model name, dim, count, catalog checksum).

The manifest exists so the matcher can refuse to load an index built from a different
catalog or a different model, rather than scoring against stale vectors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from .catalog import CATALOG_PATH, load_catalog
from .embedder import EMBED_DIM, MODEL_NAME, embed_documents

INDEX_DIR = Path(__file__).resolve().parent / "index"
VECTORS_PATH = INDEX_DIR / "catalog_index.npz"
MANIFEST_PATH = INDEX_DIR / "catalog_index.json"


def catalog_checksum(path: Path | None = None) -> str:
    """SHA-256 of the raw catalog file, so an index cannot outlive its catalog."""
    target = path or CATALOG_PATH
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(output_dir: Path | None = None) -> dict:
    target_dir = output_dir or INDEX_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    entries = load_catalog()
    print(f"catalog entries: {len(entries)}")

    started = time.perf_counter()
    vectors = embed_documents([entry.blob for entry in entries])
    elapsed = time.perf_counter() - started
    print(f"embedded in {elapsed:.1f}s -> {vectors.shape}")

    ids = np.array([entry.id for entry in entries], dtype=object)
    np.savez_compressed(target_dir / VECTORS_PATH.name, vectors=vectors, ids=ids)

    manifest = {
        "model": MODEL_NAME,
        "dim": EMBED_DIM,
        "count": len(entries),
        "catalog_sha256": catalog_checksum(),
        "build_seconds": round(elapsed, 2),
    }
    (target_dir / MANIFEST_PATH.name).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {target_dir / VECTORS_PATH.name}")
    print(f"wrote {target_dir / MANIFEST_PATH.name}")
    return manifest


def load_index() -> tuple[np.ndarray, list[str], dict]:
    """Load vectors, ids and manifest, verifying they still match the catalog.

    Raises rather than warning. A stale index produces plausible-looking but wrong
    scores, which is the worst possible failure for this module.
    """
    if not VECTORS_PATH.exists() or not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"index missing at {INDEX_DIR}. Run: python -m backend.matcher.build_index"
        )
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload = np.load(VECTORS_PATH, allow_pickle=True)
    vectors = payload["vectors"].astype(np.float32)
    ids = [str(x) for x in payload["ids"].tolist()]

    if manifest.get("model") != MODEL_NAME:
        raise RuntimeError(
            f"index was built with {manifest.get('model')!r} but embedder is pinned to "
            f"{MODEL_NAME!r}. Rebuild the index and re-tune thresholds."
        )
    if vectors.shape != (manifest["count"], EMBED_DIM):
        raise RuntimeError(f"index shape {vectors.shape} disagrees with manifest {manifest}")
    if len(ids) != vectors.shape[0]:
        raise RuntimeError("index ids and vectors have different lengths")
    actual = catalog_checksum()
    if manifest.get("catalog_sha256") != actual:
        raise RuntimeError(
            "deeplinks.json has changed since the index was built. "
            "Run: python -m backend.matcher.build_index"
        )
    return vectors, ids, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    args = parser.parse_args()
    manifest = build(args.out)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
