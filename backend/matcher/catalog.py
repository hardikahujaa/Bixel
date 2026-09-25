"""Load and normalise the Samsung deeplink catalog.

The catalog (``student_kit/deeplinks.json``) is the single source of truth for every
``bixby://`` URI we are ever allowed to emit. Nothing here invents, rewrites or
repairs a URI.

Two things in this module exist because of what the real catalog actually contains,
not because of the spec:

* 10 of the 578 entries have an empty ``qna_description`` (the appliance and
  diagnostic ones). ``build_blob`` falls back through ``message`` and
  ``description`` so no entry can produce an empty embedding.
* 111 settings exist as both an "Enable X" and a "Disable X" entry -- 222 entries,
  38% of the catalog -- whose text is near-identical. ``setting_name`` and
  ``polarity`` expose that structure so the matcher can resolve direction instead of
  guessing between two almost-identical vectors.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Repo root is three levels up: backend/matcher/catalog.py -> backend/matcher -> backend -> repo
REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "student_kit" / "deeplinks.json"

#: The one generic placeholder URI. Used only when no real entry matches.
DUMMY_DEEPLINK = "bixby://dummy_positive"

#: Leading verbs in ``message`` that carry an on/off intent.
POLARITY_VERBS: dict[str, int] = {"enable": 1, "disable": -1}

#: Leading verbs that are read-only or neutral -- no on/off intent.
NEUTRAL_VERBS: frozenset[str] = frozenset(
    {
        "view",
        "check",
        "adjust",
        "increase",
        "decrease",
        "diagnose",
        "set",
        "switch",
        "optimize",
        "open",
        "onurl",
        "offurl",
    }
)

#: Anything URL-shaped. G5 is a hard gate: zero URLs anywhere in our output, so we
#: assert the catalog is clean rather than trusting its README.
URL_PATTERN = re.compile(
    r"https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|co|html|htm|php)\b|\[[^\]]+\]\([^)]+\)",
    re.IGNORECASE,
)

#: Words too generic to identify a setting. Used when deriving grounding terms.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "to", "of", "for", "on", "off", "in", "and", "or", "your",
        "this", "that", "with", "via", "device", "settings", "setting", "page",
        "opens", "screen", "when", "all", "apps", "app", "now", "it", "is", "as",
        "from", "by", "use", "used", "using",
    }
)


@dataclass(frozen=True)
class CatalogEntry:
    """One catalog entry plus the fields we derive for matching.

    ``raw`` is the untouched dict straight out of the JSON. It is what
    ``match_deeplinks`` hands back, because projecting an entry into the response
    ``Deeplink`` shape is M2's ``to_deeplink_pair()`` and must live in exactly one
    place.
    """

    id: str
    deeplink: str
    message: str
    description: str
    qna_description: str
    original_type: str | None
    validation: dict[str, Any] | None
    raw: dict[str, Any]

    blob: str
    setting_name: str
    polarity: int
    grounding_terms: frozenset[str]

    @property
    def is_placeholder(self) -> bool:
        return self.deeplink == DUMMY_DEEPLINK

    @property
    def has_validation(self) -> bool:
        return isinstance(self.validation, dict) and bool(self.validation.get("deeplink"))


def build_blob(entry: dict[str, Any]) -> str:
    """Build the text we embed and index for one entry.

    ``qna_description`` first because it is phrased as user intent, which is the
    closest thing in the catalog to how a troubleshooting step reads. Falls back
    through the other fields so the 10 entries with an empty ``qna_description``
    still get real text.
    """
    parts: list[str] = []
    for field in ("qna_description", "message", "description"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    if not parts:  # pragma: no cover - guarded by test_catalog_all_blobs_non_empty
        raise ValueError(f"entry {entry.get('id')!r} has no usable text for a blob")
    return " | ".join(parts)


def split_message(message: str) -> tuple[str, int]:
    """Split ``message`` into (setting name, polarity).

    ``"Enable Touch sensitivity"`` -> ``("touch sensitivity", 1)``
    ``"Disable Touch sensitivity"`` -> ``("touch sensitivity", -1)``
    ``"View Reset Options"``        -> ``("reset options", 0)``

    Polarity 0 means the entry expresses no on/off intent, so it can never be the
    wrong half of a pair.
    """
    text = (message or "").strip()
    if not text:
        return "", 0
    head, _, tail = text.partition(" ")
    head_lower = head.lower()
    if head_lower in POLARITY_VERBS and tail.strip():
        return tail.strip().lower(), POLARITY_VERBS[head_lower]
    if head_lower in NEUTRAL_VERBS and tail.strip():
        return tail.strip().lower(), 0
    return text.lower(), 0


def grounding_terms(setting_name: str) -> frozenset[str]:
    """Distinctive words from a setting name, for checking it is named in a step.

    Full-text similarity gets diluted by catalog boilerplate ("opens the ... settings
    page in device Settings on the device"), which is identical across hundreds of
    entries. These terms are the part that actually identifies the setting.
    """
    words = re.findall(r"[a-z0-9]+", (setting_name or "").lower())
    return frozenset(w for w in words if len(w) > 2 and w not in _STOPWORDS)


def _coerce_entry(raw: dict[str, Any]) -> CatalogEntry:
    message = (raw.get("message") or "").strip()
    setting_name, polarity = split_message(message)
    return CatalogEntry(
        id=str(raw["id"]),
        deeplink=str(raw["deeplink"]),
        message=message,
        description=(raw.get("description") or "").strip(),
        qna_description=(raw.get("qna_description") or "").strip(),
        original_type=raw.get("originalType"),
        validation=raw.get("validation"),
        raw=raw,
        blob=build_blob(raw),
        setting_name=setting_name,
        polarity=polarity,
        grounding_terms=grounding_terms(setting_name),
    )


def load_catalog(path: Path | str | None = None) -> list[CatalogEntry]:
    """Load every catalog entry. Order is preserved and is the index order."""
    target = Path(path) if path is not None else CATALOG_PATH
    with open(target, encoding="utf-8") as handle:
        payload = json.load(handle)
    entries = payload["deeplinks"]
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{target} has no 'deeplinks' list")
    return [_coerce_entry(raw) for raw in entries]


def actionable_uris(entries: Iterable[CatalogEntry]) -> frozenset[str]:
    """Every URI we may put in ``actionableDeeplink``. One of two allowlists."""
    return frozenset(e.deeplink for e in entries)


def validation_uris(entries: Iterable[CatalogEntry]) -> frozenset[str]:
    """Every URI we may put in ``validationDeeplink``.

    A separate namespace from the actionable URIs (``masked/val`` vs ``masked/act``)
    with zero overlap, so a "did we invent a URI" check needs both sets.
    """
    out: set[str] = set()
    for entry in entries:
        validation = entry.validation
        if isinstance(validation, dict):
            uri = validation.get("deeplink")
            if isinstance(uri, str) and uri:
                out.add(uri)
    return frozenset(out)


def polarity_pairs(entries: Iterable[CatalogEntry]) -> dict[str, dict[int, str]]:
    """Settings that exist as both Enable and Disable, keyed by setting name.

    ``{"touch sensitivity": {1: "DL-0126", -1: "DL-0125"}, ...}``

    Only settings with *both* halves present are returned -- those are the ones where
    similarity alone cannot choose and polarity has to be resolved.
    """
    grouped: dict[str, dict[int, str]] = {}
    for entry in entries:
        if entry.polarity and entry.setting_name:
            grouped.setdefault(entry.setting_name, {})[entry.polarity] = entry.id
    return {name: halves for name, halves in grouped.items() if len(halves) == 2}


def contains_url(text: str) -> bool:
    """True if the text holds anything URL-shaped. Used as a G5 guard."""
    return bool(URL_PATTERN.search(text or ""))
