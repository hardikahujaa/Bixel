"""``match_deeplinks()`` -- map a troubleshooting step to catalog deeplinks, or to nothing.

The interface agreed at the Day 1 meeting:

    match_deeplinks(step_text, ctx) -> [ {catalog_id, score, entry} ]

Design constraint that drives everything here: **a wrong match costs more than a
missing one.** A missing deeplink is a small A2 coverage loss; a wrong deeplink is a
correctness failure, and a wrong deeplink promoted onto an ``auto`` action is worse
again. So this module abstains by default and only speaks when several independent
signals agree.

Why one signal is not enough -- both measured on the real catalog:

* **Lexical alone fails.** TF-IDF scores the false positive "7. Safe Mode" at 0.279
  and the true positive "Update Device Software" at 0.166. The distributions overlap.
* **Semantic alone fails too.** The weakest true positive sits at 0.707 and the
  strongest false positive at 0.704 -- a 0.003 gap. Worse, the catalog's *misleading
  messages* create confident false positives: "Perform a Factory Data Reset" scores
  0.787 against ``DL-0022`` "View Reset Options", whose qna actually describes
  auto-reset after failed unlock attempts.

So the gate combines semantic similarity, lexical overlap, and lexical *grounding* --
whether the words that actually identify the setting appear in the step at all -- and
requires them to agree. Thresholds are not guessed; ``evaluate.py`` sweeps them
against the hand-labelled set in ``labels.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .build_index import load_index
from .catalog import CatalogEntry, contains_url, load_catalog
from .embedder import cosine_scores, embed_queries
from .polarity import polarity_agrees, resolve_polarity


@dataclass(frozen=True)
class MatchContext:
    """Surrounding context for a step.

    Individual step sentences ("Tap Storage.", "Navigate to Settings.") carry no
    settings target -- the intent lives in the section they belong to. ``step_text``
    should therefore be the joined group text, and this carries the rest.
    """

    heading: str | None = None
    siis_title: str | None = None
    query: str | None = None
    polarity_hint: int | None = None

    def enrich(self, step_text: str) -> str:
        """Text used for polarity and grounding: the step plus its heading.

        The SIIS title and user query are deliberately excluded from the match text --
        they describe the *complaint*, not the action, and mixing them in pulls matches
        toward whatever the document is broadly about. That is exactly the failure that
        made "7. Safe Mode" score against touch entries.
        """
        parts = [step_text or ""]
        if self.heading:
            parts.insert(0, self.heading)
        return ". ".join(part for part in parts if part.strip())


@dataclass(frozen=True)
class MatchCandidate:
    """One catalog entry the matcher is willing to stand behind."""

    catalog_id: str
    score: float
    entry: dict[str, Any]
    polarity: int
    why: str
    signals: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Thresholds:
    """Gate parameters, selected by ``evaluate.py`` -- not chosen by hand.

    Measured on the 38-label set (5 match, 30 no-match, 3 arguable) plus 9 adversarial
    inputs:

        precision 1.000   recall 0.800   abstention accuracy 1.000
        TP=4  FN=1  FP=0  TN=30          adversarial 9/9

    The safe plateau runs from semantic 0.76 to 0.79 at grounding 0.60; 0.77 is its
    midpoint. Below 0.76 a false positive appears, and at 0.80 recall collapses to
    0.400. Sitting on a plateau edge is fragile, which is why the midpoint is taken.

    ``lexical`` is 0.0 because the sweep found it adds nothing once grounding is in
    place -- grounding already requires the words identifying a setting to be present,
    which is the useful part of lexical overlap without the noise from catalog
    boilerplate. The signal is still computed and reported in ``why`` for diagnostics.

    Caveat worth stating plainly: with 5 positives, recall moves in steps of 0.200 per
    case. The meaningful result is the *plateau* (precision holds at 1.000 across a
    range of gates while every no-match case abstains), not the third decimal place.
    Re-run ``evaluate.py`` after any change to the model, the index or the catalog.
    """

    semantic: float = 0.77
    lexical: float = 0.0
    grounding: float = 0.60
    margin: float = 0.0
    max_candidates: int = 4

    def describe(self) -> str:
        return (
            f"semantic>={self.semantic:.3f} lexical>={self.lexical:.3f} "
            f"grounding>={self.grounding:.2f} margin>={self.margin:.3f}"
        )


DEFAULT_THRESHOLDS = Thresholds()

_WORD_RE = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


class DeeplinkMatcher:
    """Holds the catalog, the committed index and the lexical model.

    Construct once per process and reuse. Building the TF-IDF model over 578 blobs is
    fast but not free, and the embedding model must never be loaded per request.
    """

    def __init__(
        self, thresholds: Thresholds | None = None, memoize_queries: bool = False
    ) -> None:
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        # Opt-in, and off in production on purpose -- app/cache.py already handles repeat
        # queries at the response level, so a second memo here would only grow memory.
        #
        # It exists for evaluate.py's threshold sweep, which calls match() with the same
        # ~47 texts once per threshold combination. Embeddings depend only on the text, so
        # without this the sweep pays 231 x 47 embeddings to compute 47 distinct vectors,
        # and the script took over ten minutes -- long enough that nobody re-runs the
        # tuning tool the module docs tell them to re-run.
        self._query_memo: dict[str, np.ndarray] | None = {} if memoize_queries else None
        self.entries: list[CatalogEntry] = load_catalog()
        vectors, ids, manifest = load_index()
        if ids != [entry.id for entry in self.entries]:
            raise RuntimeError("index ids do not align with the catalog order")
        self.vectors = vectors
        self.manifest = manifest
        self._vectorizer = TfidfVectorizer(
            sublinear_tf=True, stop_words="english", ngram_range=(1, 2)
        )
        self._lexical = self._vectorizer.fit_transform(
            [entry.blob for entry in self.entries]
        )

    # -- signals ---------------------------------------------------------------

    def _semantic(self, text: str) -> np.ndarray:
        if self._query_memo is not None:
            query = self._query_memo.get(text)
            if query is None:
                query = embed_queries([text])[0]
                self._query_memo[text] = query
        else:
            query = embed_queries([text])[0]
        return cosine_scores(query, self.vectors)

    def _lexical_scores(self, text: str) -> np.ndarray:
        query = self._vectorizer.transform([text])
        return (self._lexical @ query.T).toarray().ravel()

    def _grounding(self, text: str) -> np.ndarray:
        """Fraction of each entry's identifying words that appear in the step.

        This is the signal that catches the catalog's misleading messages. "Perform a
        Factory Data Reset" embeds close to ``DL-0022`` "View Reset Options", but the
        words that identify that entry are {reset, options} and "options" never appears
        in the step -- so grounding is partial, not full.
        """
        present = _words(text)
        out = np.zeros(len(self.entries), dtype=np.float32)
        for position, entry in enumerate(self.entries):
            terms = entry.grounding_terms
            if not terms:
                continue
            out[position] = len(terms & present) / len(terms)
        return out

    # -- public API ------------------------------------------------------------

    def match(
        self, step_text: str, ctx: MatchContext | None = None
    ) -> list[MatchCandidate]:
        if not isinstance(step_text, str) or not step_text.strip():
            return []

        context = ctx or MatchContext()
        text = context.enrich(step_text)
        if not text.strip():
            return []

        semantic = self._semantic(text)
        lexical = self._lexical_scores(text)
        grounding = self._grounding(text)

        step_polarity = (
            context.polarity_hint
            if context.polarity_hint is not None
            else resolve_polarity(text)
        )

        gate = self.thresholds
        eligible: list[int] = [
            position
            for position in range(len(self.entries))
            if semantic[position] >= gate.semantic
            and lexical[position] >= gate.lexical
            and grounding[position] >= gate.grounding
            and polarity_agrees(step_polarity, self.entries[position].polarity)
        ]
        if not eligible:
            return []

        # Fused score: semantic leads, grounding confirms. Lexical is deliberately
        # excluded from the ranking because catalog boilerplate ("opens the ...
        # settings page in device Settings on the device") is near-identical across
        # hundreds of entries and adds noise to ordering even where it passes the gate.
        fused = {position: float(semantic[position] * grounding[position]) for position in eligible}
        ranked = sorted(eligible, key=lambda position: -fused[position])

        best = fused[ranked[0]]
        if gate.margin > 0:
            runner_up = self._best_other_setting(ranked, fused)
            if runner_up is not None and best - runner_up < gate.margin:
                return []

        candidates: list[MatchCandidate] = []
        for position in ranked[: gate.max_candidates]:
            entry = self.entries[position]
            candidate = MatchCandidate(
                catalog_id=entry.id,
                score=round(fused[position], 4),
                entry=entry.raw,
                polarity=entry.polarity,
                why=(
                    f"semantic={semantic[position]:.3f} lexical={lexical[position]:.3f} "
                    f"grounding={grounding[position]:.2f} step_polarity={step_polarity:+d}"
                ),
                signals={
                    "semantic": float(semantic[position]),
                    "lexical": float(lexical[position]),
                    "grounding": float(grounding[position]),
                },
            )
            candidates.append(candidate)

        _assert_url_free(candidates)
        return candidates

    def _best_other_setting(
        self, ranked: Sequence[int], fused: dict[int, float]
    ) -> float | None:
        """Best score among entries for a *different* setting than the top one.

        Runner-up margin is meaningless against an entry's own polarity twin -- they
        are the same setting and are supposed to score alike.
        """
        top_setting = self.entries[ranked[0]].setting_name
        for position in ranked[1:]:
            if self.entries[position].setting_name != top_setting:
                return fused[position]
        return None


def _assert_url_free(candidates: Sequence[MatchCandidate]) -> None:
    """G5 is a hard gate: one URL anywhere zeroes the whole automated score.

    The catalog tests already prove it is clean, so this can only fire if a caller
    mutated an entry. Failing loudly here beats shipping a leak.
    """
    for candidate in candidates:
        for value in candidate.entry.values():
            if isinstance(value, str) and contains_url(value):
                raise RuntimeError(
                    f"catalog entry {candidate.catalog_id} contains URL-shaped text: {value!r}"
                )


_default_matcher: DeeplinkMatcher | None = None


def get_matcher() -> DeeplinkMatcher:
    """Process-wide matcher. Warm this at server startup, never on first request."""
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = DeeplinkMatcher()
    return _default_matcher


def match_deeplinks(
    step_text: str, ctx: MatchContext | None = None
) -> list[MatchCandidate]:
    """Match a troubleshooting step to catalog deeplinks.

    Returns candidates best-first, or ``[]`` when nothing clears the gate. An empty
    list is the expected answer for most steps: the catalog covers Settings toggles,
    while SIIS documents mostly instruct physical actions, app navigation and support
    escalation. There is no entry for safe mode, clearing cache, Smart Switch or screen
    mirroring, so those correctly match nothing.

    ``entry`` on each candidate is the catalog entry untouched. Projecting it into the
    response ``Deeplink`` shape is M2's ``to_deeplink_pair()``, so that rule lives in
    exactly one place.
    """
    return get_matcher().match(step_text, ctx)
