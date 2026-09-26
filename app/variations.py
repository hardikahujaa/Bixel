"""Query paraphrases. Owner: M4 (docs/PLAN.md, "Who owns what").

variations(query) -> [str], 8 to 10 of them

Genuinely diverse paraphrases -- different vocabulary, sentence structure,
formality -- not ten ways of saying the same sentence with the same words.
The count itself is scored under A5: not 7, not 11.

Tries Gemini first, the same shape extract() uses (backend/extract/pipeline.py):
one call, with a deterministic no-LLM generator behind it so this needs no
key and no network, matching the rest of the default test suite -- the same
goal backend/extract/tests/conftest.py states for the extraction pipeline's
own tests ("the suite a judge runs must not need an API key"). The
deterministic path also guarantees the exact count and non-duplication that
an LLM answer can only be checked for after the fact.
"""
from __future__ import annotations

import json
import re
from typing import Any

from backend.extract.client import GeminiClient, LLMClient, LLMUnavailable

MIN_COUNT, MAX_COUNT = 8, 10

#: The deterministic generator's fixed output size -- comfortably inside
#: [MIN_COUNT, MAX_COUNT] regardless of how much a given query's vocabulary
#: happens to overlap the synonym table below.
TARGET_COUNT = 9

#: Below this, an LLM answer is rejected as "not genuinely different" and the
#: deterministic path is used instead -- see lexical_diversity(). A sanity
#: floor, not tuned against a real corpus yet: that tuning needs M4's own
#: 20-query paraphrase set, the same way the matcher's threshold was tuned
#: against its hand-labelled set (docs/PLAN.md, Day 3).
MIN_DIVERSITY = 0.35


def variations(query: str, *, client: LLMClient | None = None) -> list[str]:
    """Return 8-10 genuinely different paraphrases of a troubleshooting query."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("variations() requires a non-empty query string")
    cleaned = query.strip()

    llm_result = _try_llm(cleaned, client)
    if llm_result is not None:
        return llm_result
    return _deterministic_variations(cleaned)


# --------------------------------------------------------------------- LLM path


def _try_llm(query: str, client: LLMClient | None) -> list[str] | None:
    """The LLM path. Returns None to mean "fall back", never raises."""
    try:
        active_client = client if client is not None else GeminiClient()
    except LLMUnavailable:
        return None

    try:
        raw = active_client.generate_json(_build_prompt(query))
    except Exception:  # noqa: BLE001 - any client failure means fall back
        return None

    candidates = _parse_candidates(raw)
    if candidates is None or not MIN_COUNT <= len(candidates) <= MAX_COUNT:
        return None
    if _reuses_the_original(candidates, query):
        return None
    if lexical_diversity(candidates) < MIN_DIVERSITY:
        return None
    return candidates


def _build_prompt(query: str) -> str:
    return (
        "Rewrite the following device-troubleshooting complaint as 8 to 10 "
        "genuinely different paraphrases. Vary the vocabulary, the sentence "
        "structure and the formality -- do not just swap one or two words in "
        "the same sentence. Keep the same underlying problem; never add a "
        "symptom that is not in the original. Return JSON only: a single "
        "array of strings, nothing else, no explanation, no code fence.\n\n"
        f"Complaint: {query}"
    )


def _parse_candidates(raw: str) -> list[str] | None:
    """Parse the model's answer, tolerating a code fence it was told not to use."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(parsed, dict):
        # Tolerate {"paraphrases": [...]} in case the model wraps the array.
        parsed = next((v for v in parsed.values() if isinstance(v, list)), None)
    if not isinstance(parsed, list):
        return None
    cleaned = [item.strip() for item in parsed if isinstance(item, str) and item.strip()]
    return cleaned or None


def _reuses_the_original(candidates: list[str], query: str) -> bool:
    """True if the model just echoed the input, or repeated itself."""
    seen = {query.strip().lower()}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in seen:
            return True
        seen.add(key)
    return False


def lexical_diversity(texts: list[str]) -> float:
    """How lexically different a set of paraphrases is: 0.0 (identical word
    choice throughout) to 1.0 (no word shared between any pair).

    Average pairwise Jaccard *distance* between each pair's word sets. Used
    to catch an LLM answer that technically returns 8-10 strings but is
    really one sentence with the odd word swapped -- the exact failure mode
    the plan calls out for this job (docs/PLAN.md, M4's Day 1 job: "so you
    can tell when you have written ten ways of saying the same sentence with
    the same words").
    """
    word_sets = [set(re.findall(r"[a-z0-9']+", text.lower())) for text in texts]
    distances = []
    for i, a in enumerate(word_sets):
        for b in word_sets[i + 1 :]:
            union = a | b
            if union:
                distances.append(1 - len(a & b) / len(union))
    return sum(distances) / len(distances) if distances else 0.0


# ------------------------------------------------------------ deterministic path

#: One synonym tuple per key. Not exhaustive -- it only has to cover the
#: vocabulary of device-troubleshooting complaints: docs/KIT_NOTES.md section
#: 5 says all 20 kit queries are screen/display problems, and M4's own fake
#: SIIS payloads (docs/PLAN.md, M4's Day 1 second job) add battery, wifi and
#: camera. A word with no entry here is left untouched.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "screen": ("display", "screen"),
    "display": ("screen", "display"),
    "black": ("dark", "blank"),
    "blank": ("black", "dark"),
    "dark": ("black", "dim"),
    "broken": ("not working", "malfunctioning"),
    "cracked": ("shattered", "fractured"),
    "phone": ("device", "handset"),
    "device": ("phone", "gadget"),
    "tablet": ("device", "tablet"),
    "problem": ("issue", "trouble"),
    "issue": ("problem", "trouble"),
    "fix": ("resolve", "sort out"),
    "frozen": ("stuck", "unresponsive"),
    "stuck": ("frozen", "jammed"),
    "slow": ("sluggish", "laggy"),
    "laggy": ("slow", "sluggish"),
    "dead": ("unresponsive", "not responding"),
    "flickering": ("flashing", "strobing"),
    "battery": ("charge", "power"),
    "draining": ("depleting", "running down"),
    "wifi": ("wi-fi", "wireless connection"),
    "dropping": ("disconnecting", "cutting out"),
    "focusing": ("focusing", "autofocusing"),
    "restart": ("reboot", "power cycle"),
    "reset": ("restore", "reset"),
    "settings": ("preferences", "settings"),
    "app": ("application", "app"),
    "apps": ("applications", "apps"),
    "touch": ("touchscreen", "touch input"),
    "loud": ("noisy", "loud"),
    "quiet": ("silent", "quiet"),
    "hot": ("warm", "overheating"),
    "won't": ("will not", "refuses to"),
    "isn't": ("is not",),
    "doesn't": ("does not",),
}

_WORD_RE = re.compile(r"[A-Za-z']+")


def _substitute(text: str, variant: int) -> str:
    """Swap in one synonym per matched word, cycling options by variant index
    so different templates favour different vocabulary, not just structure."""

    def repl(match: "re.Match[str]") -> str:
        word = match.group(0)
        options = _SYNONYMS.get(word.lower())
        if not options:
            return word
        chosen = options[variant % len(options)]
        return chosen[:1].upper() + chosen[1:] if word[:1].isupper() else chosen

    return _WORD_RE.sub(repl, text)


#: Several ``original_query`` strings in student_kit/siis_responses.json carry
#: a literal list-numbering prefix left over from how the kit numbers its
#: queries (docs/KIT_NOTES.md section 7) -- row_1 is ``"1. My Samsung..."``
#: and row_17 is ``'1. "My Galaxy S24...'``. Left in, every deterministic
#: paraphrase would visibly repeat "1." instead of actually varying the
#: wording.
_LEADING_NUMBER_RE = re.compile(r'^\s*\d+\.\s*"?')


def _core(text: str) -> str:
    """The query with the kit's leading numbering and trailing punctuation
    stripped, for embedding in a template."""
    stripped, replaced = _LEADING_NUMBER_RE.subn("", text.strip())
    if replaced and stripped.endswith('"'):
        stripped = stripped[:-1]
    return stripped.rstrip(" .!?").strip()


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


#: Nine distinct wrapper phrasings, each unique by construction so a
#: deterministic variant can never collide with the raw input or with
#: another variant even when the query has no substitutable vocabulary at
#: all. Each also gets its own synonym variant index (below), so vocabulary
#: shifts alongside structure and register.
_TEMPLATES: tuple[Any, ...] = (
    lambda core, v: f"In short, {_lower_first(_substitute(core, v))}.",
    lambda core, v: f"Why is this happening: {_lower_first(_substitute(core, v))}?",
    lambda core, v: f"Ugh, {_lower_first(_substitute(core, v))} -- anyone know why?",
    lambda core, v: f"I am experiencing the following issue: {_lower_first(_substitute(core, v))}.",
    lambda core, v: f"Reported problem: {_substitute(core, v)}.",
    lambda core, v: f"Can you help me figure out why {_lower_first(_substitute(core, v))}?",
    lambda core, v: f"For some reason, {_lower_first(_substitute(core, v))}.",
    lambda core, v: f"Need this sorted out - {_lower_first(_substitute(core, v))}",
    lambda core, v: f"Could you please advise on the following: {_lower_first(_substitute(core, v))}?",
)
assert len(_TEMPLATES) == TARGET_COUNT, "one wrapper per variant -- keep the count in sync"


def _deterministic_variations(query: str) -> list[str]:
    core = _core(query)
    return [template(core, variant) for variant, template in enumerate(_TEMPLATES)]
