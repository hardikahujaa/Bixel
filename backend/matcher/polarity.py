"""Resolve whether a step wants a setting turned on or off.

This exists because 111 settings -- 222 entries, 38% of the catalog -- appear as both
an "Enable X" and a "Disable X" entry, and the two share an *identical*
``qna_description``. ``DL-0125`` and ``DL-0126`` both read "Increases touch
sensitivity for better screen response when using screen protectors or gloves."

So no amount of embedding quality can tell them apart. The only signal is the verb in
``message``, matched against intent in the step text. Without this, a correct setting
comes back with the wrong direction roughly half the time.

When the text gives no direction, this returns 0 (undecided) and the matcher returns
both halves rather than picking one. An honest ambiguity beats a coin flip.
"""

from __future__ import annotations

import re

#: Phrases that mean "switch this off". Ordered longest-first within each group so
#: "turn off" is found before a bare "off".
_NEGATIVE = (
    r"\bturn(?:ing|s)?\s+(?:\w+\s+)?off\b",
    r"\bswitch(?:ing|es)?\s+(?:\w+\s+)?off\b",
    r"\bdisabl\w*",
    r"\bdeactivat\w*",
    r"\buncheck\w*",
    r"\btoggl\w*\s+off\b",
    r"\bset\s+\w+\s+to\s+off\b",
    r"\boff\b",
)

#: Phrases that mean "switch this on".
#:
#: Every alternative is ``\b``-anchored on purpose. Without it ``activat\w*`` matches
#: inside "de-activate", so "Deactivate Super steady mode" fired both directions and
#: resolved to Enable -- a wrong deeplink from a one-word instruction.
_POSITIVE = (
    r"\bturn(?:ing|s)?\s+(?:\w+\s+)?on\b",
    r"\bswitch(?:ing|es)?\s+(?:\w+\s+)?on\b",
    r"\benabl\w*",
    r"\bactivat\w*",
    # "Make sure auto rotate is on" -- the setting name can be several words, so an
    # earlier \w+ here matched nothing and the cue was missed entirely.
    r"\bmake\s+sure\s+[\w\s-]{0,40}?\bis\s+on\b",
    r"\btoggl\w*\s+on\b",
    r"\bon\b",
)

_NEGATIVE_RE = re.compile("|".join(_NEGATIVE), re.IGNORECASE)
_POSITIVE_RE = re.compile("|".join(_POSITIVE), re.IGNORECASE)

#: A bare "on"/"off" is far weaker evidence than "turn off"/"disable", because "on"
#: appears constantly in ordinary prose ("on the screen", "on your phone").
_WEAK_RE = re.compile(r"\b(?:on|off)\b", re.IGNORECASE)


def resolve_polarity(text: str) -> int:
    """Return +1 (enable), -1 (disable) or 0 (undecided).

    Strong cues beat weak ones. If strong cues point both ways, or only weak evidence
    exists, the answer is 0 and the caller should keep both halves of the pair.
    """
    if not text or not text.strip():
        return 0

    strong_negative = _strong_hits(text, _NEGATIVE_RE)
    strong_positive = _strong_hits(text, _POSITIVE_RE)

    if strong_negative and not strong_positive:
        return -1
    if strong_positive and not strong_negative:
        return 1
    if strong_negative and strong_positive:
        # Both directions appear. "If X is enabled... to turn off this feature" is the
        # real shape in the SIIS touch-sensitivity section, where the *instruction* is
        # the later clause. Prefer whichever cue appears last.
        last_negative = max(m.end() for m in _NEGATIVE_RE.finditer(text) if _is_strong(m))
        last_positive = max(m.end() for m in _POSITIVE_RE.finditer(text) if _is_strong(m))
        return -1 if last_negative > last_positive else 1
    return 0


def _is_strong(match: re.Match[str]) -> bool:
    """A match is strong unless it is a bare 'on' or 'off'."""
    return not _WEAK_RE.fullmatch(match.group(0).strip())


def _strong_hits(text: str, pattern: re.Pattern[str]) -> int:
    return sum(1 for match in pattern.finditer(text) if _is_strong(match))


def polarity_agrees(step_polarity: int, entry_polarity: int) -> bool:
    """Is an entry's direction compatible with what the step asked for?

    A neutral entry (``View``/``Adjust``/``Check``) is always compatible -- it has no
    direction to contradict. An undecided step is compatible with everything, which is
    what makes the matcher return both halves of a pair.
    """
    if entry_polarity == 0 or step_polarity == 0:
        return True
    return step_polarity == entry_polarity
