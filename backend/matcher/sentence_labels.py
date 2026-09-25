"""Siddhant's 29 sentence-level labels, as a second scored dataset.

`labels.py` holds 38 **group-level** labels -- whole `##` sections of a SIIS document.
This module holds 29 **sentence-level** labels: single instruction sentences lifted from
`siis_responses.json`. Both matter because it is not yet settled what granularity
`extract()` will hand the matcher, and the two datasets stress different behaviour:

* group-level text is long, so the risk is incidental keyword overlap producing a
  confident wrong match
* sentence-level text is short and specific, so the risk is missing a real match

Provenance: authored by Siddhant in `4e7f8c8` alongside his phrase-gate matcher, deleted
in `af8bdd2` when that matcher was replaced. The labels are restored unmodified -- they
are independent test data, and a cross-evaluation measured his matcher scoring recall
0.857 against them versus 0.714 for this one, at equal precision. Keeping them means that
gap stays visible instead of being quietly forgotten.

Integrity, measured rather than assumed: 26 of the 29 `step_text` values are byte-exact
substrings of their own row's content, and the remaining 3 match after whitespace
normalisation (his readme calls them "minimally trimmed clauses"). None is invented. The
check below uses normalisation so it passes honestly while still catching a fabricated
label.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .catalog import REPO_ROOT

SENTENCE_LABELS_PATH = REPO_ROOT / "app" / "matcher_labels.json"
SIIS_PATH = REPO_ROOT / "student_kit" / "siis_responses.json"


@dataclass(frozen=True)
class SentenceLabel:
    row_id: str
    step_text: str
    acceptable_ids: frozenset[str]
    note: str

    @property
    def expects_match(self) -> bool:
        """An empty acceptable list means the step legitimately has no catalog entry."""
        return bool(self.acceptable_ids)


def _normalise(text: str) -> str:
    return " ".join((text or "").split())


@lru_cache(maxsize=1)
def load_sentence_labels() -> tuple[SentenceLabel, ...]:
    with open(SENTENCE_LABELS_PATH, encoding="utf-8") as handle:
        payload = json.load(handle)
    return tuple(
        SentenceLabel(
            row_id=item["row_id"],
            step_text=item["step_text"],
            acceptable_ids=frozenset(item["acceptable_catalog_ids"]),
            note=item.get("note", ""),
        )
        for item in payload["labels"]
    )


@lru_cache(maxsize=1)
def siis_content_by_row() -> dict[str, str]:
    with open(SIIS_PATH, encoding="utf-8") as handle:
        payload = json.load(handle)
    return {row["id"]: row["siis_response"]["content"] for row in payload["responses"]}


def traces_to_source(label: SentenceLabel) -> bool:
    """Is this label's text really present in the SIIS row it claims to come from?

    This is what makes the dataset trustworthy: a label whose text cannot be found in the
    source is either mistyped or invented, and either way it should not be scored.
    """
    content = siis_content_by_row().get(label.row_id)
    if content is None:
        return False
    return _normalise(label.step_text) in _normalise(content)


def counts() -> dict[str, int]:
    labels = load_sentence_labels()
    positive = sum(1 for label in labels if label.expects_match)
    return {"total": len(labels), "match": positive, "none": len(labels) - positive}
