"""Deterministic fallback plan, built from the SIIS document itself. No LLM involved.

This exists to make one project-wide rule unconditional: **contexts is never empty.** An
empty ``contexts`` is schema-valid, so it sails through gates G4 and G5 while scoring zero
on A4 generalization -- the worst possible outcome, because nothing fails loudly.

It runs whenever the model route cannot produce something trustworthy:

* no API key configured
* every model in the chain returned 503 (measured as a real risk -- see ``client.py``)
* the response would not parse as JSON, twice
* ``validate()`` rejected the response twice
* grounding rejected every step, meaning the model answered from general knowledge

Because it only reads the supplied text, its output is grounded by construction: every
step is a line copied from the document it was given.

The word-count arithmetic here is deliberately mechanical rather than elegant. ``title``
must be 2-3 words and ``description`` 5-7 words starting "It will", and those are scored
under A1 -- so the helpers below *construct* strings in range instead of hoping a phrase
happens to fit.
"""

from __future__ import annotations

import re
from typing import Any

#: Words that carry no meaning for a title or a goal name.
_NOISE = frozenset({
    "a", "an", "the", "on", "in", "of", "for", "to", "and", "or", "your", "you", "is",
    "are", "be", "with", "from", "at", "by", "it", "its", "this", "that", "these",
    "samsung", "galaxy", "phone", "tablet", "device", "step", "steps", "check", "first",
    "things", "some", "how", "what", "when", "use", "using", "do", "does",
})

#: Padding used only when a constructed phrase is under the minimum word count.
_PADDING = ("now", "safely", "correctly", "properly", "again")

_WORD_RE = re.compile(r"[A-Za-z0-9]+")

#: Lines that read like an instruction rather than prose. Same verb set the matcher's
#: group extraction uses, kept local so the two can diverge without breaking each other.
_IMPERATIVE = re.compile(
    r"^(Navigate|Tap|Select|Touch|Swipe|Press|Go to|Turn|Open|Clear|Enable|Disable|"
    r"Choose|Scroll|Hold|Check|Connect|Restart|Remove|Slide|Drag|Set|Adjust|Try|"
    r"Ensure|Make sure|Contact|Back up|Uninstall|Reinstall|Update|Charge|Plug|Insert)\b",
    re.IGNORECASE,
)

MAX_ACTIONS = 4
MAX_STEPS_PER_GROUP = 4


def _keywords(text: str, limit: int = 8) -> list[str]:
    words = [w for w in _WORD_RE.findall(text or "") if w.lower() not in _NOISE and len(w) > 2]
    seen: set[str] = set()
    out: list[str] = []
    for word in words:
        low = word.lower()
        if low not in seen:
            seen.add(low)
            out.append(word)
        if len(out) >= limit:
            break
    return out


def make_title(source: str) -> str:
    """A 2-3 word title. Constructed to fit, not hoped into range."""
    words = _keywords(source, limit=3)
    if not words:
        words = ["Device", "issue"]
    if len(words) == 1:
        words.append("issue")
    words = words[:3]
    return " ".join([words[0].capitalize()] + [w.lower() for w in words[1:]])


def make_goal_name(source: str) -> str:
    """The ``<Name>`` slot in the goal sentence. One or two words, title-cased."""
    words = _keywords(source, limit=2) or ["Device"]
    return " ".join(w.capitalize() for w in words)


def make_goal(source: str) -> str:
    """The exact goal sentence, trailing period included."""
    return f"Follow these steps to perform this {make_goal_name(source)} Troubleshooting."


#: Action verbs worth leading a description with. Kept separate from ``_NOISE``, which
#: exists for titles: "check" is noise in a title but is the whole point of a description.
_LEAD_VERBS = (
    "check", "clear", "restart", "charge", "update", "adjust", "contact", "verify",
    "review", "force", "enable", "disable", "turn", "open", "select", "remove",
    "connect", "back", "reset", "attempt", "perform", "transfer", "mirror", "customize",
    "exit", "create", "test", "inspect",
)

#: Leading numbering the SIIS headings use: "Step 4: ...", "2. ...".
_HEADING_NUMBER = re.compile(r"^\s*(?:step\s*)?\d+\s*[.:)-]\s*", re.IGNORECASE)


def make_description(source: str) -> str:
    """A 5-7 word description starting "It will", that also reads as English.

    Constructed rather than hoped into range, because A1 scores the count exactly and a
    near-miss is worth the same as garbage. Leading with the heading's own verb is what
    keeps it readable: without it, "Check for Physical Damage" collapsed into
    "It will physical damage liquid exposure", which satisfies every rule and is not a
    sentence.
    """
    cleaned = _HEADING_NUMBER.sub("", source or "")
    lowered = [w.lower() for w in _WORD_RE.findall(cleaned)]
    verb = next((w for w in lowered if w in _LEAD_VERBS), None)

    keywords = [w.lower() for w in _keywords(cleaned, limit=5)]
    if verb:
        keywords = [w for w in keywords if w != verb]

    body = ([verb] if verb else []) + keywords
    words = ["It", "will"] + body
    if len(words) < 5:
        # "It will charge" -> "It will help you charge": pads to length AND reads better.
        words = ["It", "will", "help", "you"] + body
    index = 0
    while len(words) < 5:
        words.append(_PADDING[index % len(_PADDING)])
        index += 1
    return " ".join(words[:7])


def make_action_name(heading: str) -> str:
    words = _keywords(heading, limit=4) or ["Follow", "guidance"]
    return " ".join(w.capitalize() for w in words)


def split_sections(content: str) -> list[tuple[str, list[str]]]:
    """Split SIIS content into (heading, lines) pairs on ``##`` markers.

    Content with no ``##`` headings at all still yields one section, because several kit
    rows are flat prose and returning nothing here would defeat the whole purpose.
    """
    sections: list[tuple[str, list[str]]] = []
    heading: str | None = None
    body: list[str] = []
    for raw in (content or "").split("\n"):
        line = raw.strip()
        if line.startswith("##"):
            if heading and body:
                sections.append((heading, body))
            heading = line.lstrip("#").strip()
            body = []
        elif line and not line.startswith("#"):
            body.append(line)
    if heading and body:
        sections.append((heading, body))

    if not sections:
        lines = [l.strip() for l in (content or "").split("\n") if l.strip() and not l.startswith("#")]
        if lines:
            sections.append(("Recommended checks", lines))
    return sections


def _pick_steps(lines: list[str]) -> list[str]:
    """Prefer imperative lines; fall back to the first prose lines.

    Either way the text is copied from the document, so the result is grounded.
    """
    imperative = [l for l in lines if _IMPERATIVE.match(l) and 8 <= len(l) <= 220]
    chosen = imperative or [l for l in lines if 8 <= len(l) <= 220]
    return chosen[:MAX_STEPS_PER_GROUP] or ["Review the guidance for this device issue."]


def build_fallback(query: str, siis_response: dict[str, Any]) -> dict[str, Any]:
    """A valid, non-empty, fully grounded response built only from the supplied document."""
    siis = siis_response if isinstance(siis_response, dict) else {}
    title_source = (siis.get("title") or "").strip()
    content = siis.get("content") or ""
    naming_source = title_source or (query if isinstance(query, str) else "") or "Device issue"

    sections = split_sections(content)[:MAX_ACTIONS]

    actions: list[dict[str, Any]] = []
    for heading, lines in sections:
        actions.append(
            {
                "actionName": make_action_name(heading),
                "description": make_description(heading),
                # Always manual: the fallback proposes no deeplink, and only "auto"
                # actions are required to carry one.
                "category": "manual",
                "stepGroups": [
                    {
                        "steps": _pick_steps(lines),
                        "actionableDeeplink": None,
                        "validationDeeplink": None,
                    }
                ],
            }
        )

    if not actions:
        # Nothing usable in the document at all -- still must not return empty contexts.
        actions.append(
            {
                "actionName": "Contact Samsung Support",
                "description": make_description(naming_source),
                "category": "manual",
                "stepGroups": [
                    {
                        "steps": ["Contact Samsung Support to arrange further assistance."],
                        "actionableDeeplink": None,
                        "validationDeeplink": None,
                    }
                ],
            }
        )

    return {
        "contexts": [
            {
                "goal": make_goal(naming_source),
                "title": make_title(naming_source),
                # Honest confidence: this path did no reasoning about the query.
                "score": 0.35,
                "actions": actions,
            }
        ]
    }
