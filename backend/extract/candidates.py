"""Pre-compute catalog candidates for each section of a SIIS document.

This is the mechanism that makes a hallucinated deeplink **structurally impossible** rather
than something we catch afterwards.

The model never sees a ``bixby://`` string and is never asked to produce one. Instead:

1. the document is split into its ``##`` sections
2. ``match_deeplinks()`` runs on each section *before* the LLM call
3. the model is shown a **numbered list** of plain-English candidate labels and must answer
   with a number or ``"none"``
4. the server maps that number back to the catalog entry and projects it with M2's
   ``to_deeplink_pair()``

So the model's output space contains no URI at all. The only URIs that can appear in a
response are ones looked up from the catalog by index.

Import note: ``match_deeplinks`` is imported from ``backend.matcher.matcher``, the path
documented in ``backend/matcher/README.md``. The team's matcher reconciliation is still
open, so if the implementation moves, this import is the single line to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .fallback import split_sections

#: How many candidates to offer per section. More than a handful and the model starts
#: pattern-matching on the list instead of on the text; the matcher's own cap is 4.
MAX_OPTIONS_PER_SECTION = 4

#: Sections beyond this are dropped. Keeps the prompt bounded -- one kit document has 11
#: sections, and a prompt that large costs latency for no gain in answer quality.
MAX_SECTIONS = 6


@dataclass(frozen=True)
class CandidateOption:
    """One offered catalog entry.

    ``number`` and ``label`` are all the model sees. ``catalog_id``, ``entry`` and
    ``score`` stay server-side.
    """

    number: int
    label: str
    catalog_id: str
    entry: dict[str, Any]
    score: float
    polarity: int

    @property
    def prompt_line(self) -> str:
        return f"{self.number}. {self.label}"


@dataclass(frozen=True)
class SectionCandidates:
    """One ``##`` section of the document, with its offered options."""

    number: int
    heading: str
    lines: tuple[str, ...]
    options: tuple[CandidateOption, ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        return f"{self.heading}. " + " ".join(self.lines)

    def option_by_number(self, number: Any) -> CandidateOption | None:
        """Resolve the model's answer. Anything unrecognised resolves to None.

        Deliberately forgiving about type -- the model may answer ``2``, ``"2"`` or
        ``"none"`` -- and deliberately strict about range, so an out-of-range index
        becomes "no deeplink" rather than an arbitrary entry.
        """
        if number is None:
            return None
        try:
            wanted = int(str(number).strip())
        except (TypeError, ValueError):
            return None
        for option in self.options:
            if option.number == wanted:
                return option
        return None


def _label_for(entry: dict[str, Any]) -> str:
    """A plain-English description of an entry, with no URI in it.

    ``message`` alone is not enough: 194 of the 578 entries share a message with another
    entry ("View Notification Settings" appears 21 times), and several are actively
    misleading -- "Disable Charging" actually means vibration feedback while charging. The
    ``qna_description`` is what actually identifies the setting, so both are shown.
    """
    message = (entry.get("message") or "").strip()
    qna = (entry.get("qna_description") or entry.get("description") or "").strip()
    if qna and message:
        return f"{message} -- {qna}"
    return message or qna or "Unlabelled settings entry"


def build_candidates(
    siis_response: dict[str, Any],
    matcher: Callable[..., Sequence[Any]] | None = None,
    max_sections: int = MAX_SECTIONS,
    max_options: int = MAX_OPTIONS_PER_SECTION,
) -> list[SectionCandidates]:
    """Split the document and attach catalog candidates to each section.

    ``matcher`` is injectable so tests can run without loading the embedding model, and so
    a failure inside the matcher degrades to "no candidates" rather than failing the whole
    request -- a section with no options simply cannot produce a deeplink, which is a safe
    outcome.
    """
    siis = siis_response if isinstance(siis_response, dict) else {}
    sections = split_sections(siis.get("content") or "")[:max_sections]
    if not sections:
        return []

    match = matcher if matcher is not None else _default_matcher()

    out: list[SectionCandidates] = []
    for index, (heading, lines) in enumerate(sections, start=1):
        section = SectionCandidates(number=index, heading=heading, lines=tuple(lines))
        options: list[CandidateOption] = []
        try:
            results = match(section.text, heading=heading) or []
        except Exception:  # noqa: BLE001 - no candidates beats no response
            results = []
        for offset, candidate in enumerate(results[:max_options], start=1):
            entry = getattr(candidate, "entry", None) or {}
            options.append(
                CandidateOption(
                    number=offset,
                    label=_label_for(entry),
                    catalog_id=getattr(candidate, "catalog_id", entry.get("id", "")),
                    entry=entry,
                    score=float(getattr(candidate, "score", 0.0)),
                    polarity=int(getattr(candidate, "polarity", 0)),
                )
            )
        out.append(
            SectionCandidates(
                number=index, heading=heading, lines=tuple(lines), options=tuple(options)
            )
        )
    return out


def _default_matcher() -> Callable[..., Sequence[Any]]:
    """Adapter onto the documented matcher interface.

    Wrapped rather than called directly so the import stays in one place and the keyword
    shape here (``text, heading=...``) does not have to change if the matcher's own
    signature evolves.
    """
    from backend.matcher.matcher import MatchContext, match_deeplinks

    def match(text: str, heading: str | None = None):
        return match_deeplinks(text, MatchContext(heading=heading))

    return match


def render_for_prompt(sections: Sequence[SectionCandidates]) -> str:
    """The section-and-candidate block that goes into the prompt.

    Sections are numbered so the model can attribute each action to its source, and
    candidates are numbered per section so an answer of ``2`` is unambiguous.
    """
    blocks: list[str] = []
    for section in sections:
        lines = "\n".join(f"    - {line}" for line in section.lines)
        if section.options:
            offered = "\n".join(f"    {option.prompt_line}" for option in section.options)
        else:
            offered = "    (no catalog entry matches this section -- you must answer none)"
        blocks.append(
            f"SECTION {section.number}: {section.heading}\n"
            f"  Lines you may use as steps:\n{lines}\n"
            f"  Settings shortcuts available for this section:\n{offered}"
        )
    return "\n\n".join(blocks)
