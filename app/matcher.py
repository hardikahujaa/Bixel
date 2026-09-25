"""Deeplink matcher. Owner: M1 (Claude.md section 8).

match_deeplinks(step_text, ctx) -> [{catalog_id, score, entry}]

## Architecture: phrase gate decides abstain/don't, cosine ranks survivors

A step is a candidate for catalog entry E iff E's `message`, with its
leading Enable/Disable/View/Adjust/Check verb stripped, appears verbatim
(case-insensitive) in the step text -- and isn't on the small denylist of
generic hardware/UI nouns below. Candidates are then ranked by TF-IDF
cosine similarity against qna_description (only used for ranking multiple
survivors, not for deciding abstain/don't).

This two-stage design exists because pure cosine similarity was measured
and rejected first. TF-IDF is fit only on the catalog's own text, so
generic navigation chrome ("go to Settings, tap X, then tap Y") is RARE in
that corpus and gets inflated IDF weight in a query vector, drowning the
real signal: at every threshold from 0.10-0.50, cosine-only precision never
exceeded 0.50 on the hand-labeled set, and getting to 0.50 cost recall down
to 0.17. Positive and negative cosine scores overlap almost entirely --
there is no threshold that separates them (see
scripts/measure_matcher_precision.py for the full sweep).

The phrase gate, by contrast, is nearly free of false positives once the
denylist removes single generic hardware nouns ("Volume", "Side button",
"Home screen", "top right" -- these are themselves whole catalog messages
after their verb prefix is stripped, and they show up as incidental phrases
in totally unrelated steps like restart instructions).

## Measured result (scripts/measure_matcher_precision.py, 29-example
   hand-labeled set pulled from real SIIS step sentences):
   precision = 1.00, recall = 0.86 (tp=6, fp=0, tn=22, fn=1)

## Known limitation, not fixed today (Claude.md Day 3 is "tune the abstain
   threshold" -- this is exactly that kind of work):
   The one miss is a real catalog data-quality issue, not a matcher bug.
   "go to Settings, tap Connections, and then tap Wi-Fi" should match
   DL-0313 ("View WiFi Settings", qna: "Controls Wi-Fi to connect to
   wireless networks..."). But FIVE other catalog entries also carry the
   message "View/Enable/Disable WiFi" while describing entirely different
   features (Wi-Fi scanning while off, for location accuracy) -- so a
   message-text phrase gate can't disambiguate them, and cosine ranks the
   wrong ones higher (0.57-0.59) than the right one (0.40) because their
   qna_description happens to share more incidental words with a generic
   step sentence. Fixing this needs qna_description-level semantic
   disambiguation, i.e. exactly what a neural embedding would add value on
   -- deferred to Day 3 rather than adding network/model-download risk to
   a Day 1 scaffold for one labeled example.
"""
import json
import re
from pathlib import Path
from typing import Any, Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

KIT_ROOT = Path(__file__).resolve().parent.parent / "student_kit"
DEEPLINKS_PATH = KIT_ROOT / "deeplinks.json"

_MESSAGE_PREFIX = re.compile(r"^(enable|disable|view|adjust|check)\s+", re.IGNORECASE)

# Empirically found false positives (scripts/measure_matcher_precision.py):
# single generic hardware/UI nouns that happen to be a whole catalog message
# once its verb prefix is stripped, and that show up as incidental phrases
# in unrelated steps (e.g. "Adjust Volume" -> "volume" fires on "Volume down
# button" in a force-restart instruction that has nothing to do with the
# volume/sound-mode setting). Extend this list as more are found.
GENERIC_MESSAGE_PHRASES = {
    "volume", "side button", "home screen", "top right", "bottom right",
    "top left", "bottom left",
}


def _entry_text(entry: dict) -> str:
    # qna_description is phrased as user intent and is the best rank target
    # (Claude.md section 4); message is a short imperative label; description
    # is boilerplate-shaped ("Opens the X settings page...") and adds mostly
    # noise, so it's weighted down by not being repeated like qna_description.
    qna = entry.get("qna_description") or ""
    message = entry.get("message") or ""
    description = entry.get("description") or ""
    return " ".join(filter(None, [qna, qna, message, description]))


def _message_phrase(entry: dict) -> str:
    return _MESSAGE_PREFIX.sub("", entry.get("message") or "").strip().lower()


class CatalogIndex:
    def __init__(self, path: Path = DEEPLINKS_PATH):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.entries = data["deeplinks"]
        texts = [_entry_text(e) for e in self.entries]
        self.vectorizer = TfidfVectorizer(lowercase=True, stop_words="english", ngram_range=(1, 2))
        self.matrix = self.vectorizer.fit_transform(texts)
        self._entry_positions = {id(e): i for i, e in enumerate(self.entries)}

    def phrase_gate(self, step_text: str) -> list[dict]:
        lowered = step_text.lower()
        return [
            entry
            for entry in self.entries
            if (phrase := _message_phrase(entry))
            and phrase not in GENERIC_MESSAGE_PHRASES
            and phrase in lowered
        ]

    def cosine_score(self, step_text: str, entry_index: int) -> float:
        query_vec = self.vectorizer.transform([step_text])
        return float(cosine_similarity(query_vec, self.matrix[entry_index])[0][0])

    def search(self, step_text: str, top_k: int = 5) -> list[tuple[dict, float]]:
        """Cosine-ranked search over the WHOLE catalog, ignoring the phrase
        gate. Not used by match_deeplinks() -- kept for
        scripts/measure_matcher_precision.py, which documents why cosine
        alone was rejected as the abstain signal."""
        query_vec = self.vectorizer.transform([step_text])
        sims = cosine_similarity(query_vec, self.matrix)[0]
        ranked = sims.argsort()[::-1][:top_k]
        return [(self.entries[i], float(sims[i])) for i in ranked]


_index: Optional[CatalogIndex] = None


def _get_index() -> CatalogIndex:
    global _index
    if _index is None:
        _index = CatalogIndex()
    return _index


def match_deeplinks(step_text: str, ctx: Optional[dict] = None, top_k: int = 3) -> list[dict[str, Any]]:
    """ctx (the surrounding SIIS document / query) is accepted per the agreed
    signature but unused for now -- no case in the labeled set has needed it
    for disambiguation. Revisit if a future hard case does."""
    index = _get_index()
    candidates = index.phrase_gate(step_text)
    if not candidates:
        return []

    scored = [
        {"catalog_id": e["id"], "score": index.cosine_score(step_text, index._entry_positions[id(e)]), "entry": e}
        for e in candidates
    ]
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top_k]
