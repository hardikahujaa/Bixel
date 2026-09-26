"""Verify that every generated step actually came from the supplied SIIS text.

``validate()`` checks *formatting*. It cannot tell whether a step was lifted from the
document or invented from the model's general knowledge of Samsung phones -- and that
second failure is the one A4's hidden payloads punish, because on an unseen document a
model that answers from memory produces confident, well-formatted, wrong instructions.

Two tiers, because the prompt asks for lines "or a very close paraphrase":

1. **containment** -- the step, whitespace-normalised and lowercased, appears in the
   document. This is the clean case and most steps hit it.
2. **token overlap** -- the step shares enough distinctive words with some single line of
   the document. This catches a legitimate light rewrite ("Tap Apps, then Storage" from
   "Tap Apps." + "Tap Storage.") without accepting a sentence built from nothing.

Anything that clears neither is dropped. Dropping is the right response rather than
failing the request: a plan missing one unverifiable step is still useful, whereas a plan
containing an invented step is actively misleading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Fraction of a step's distinctive words that must appear in one source line.
#:
#: Calibrated, not guessed. Measured against 83 steps the fallback genuinely copied out of
#: the 20 kit documents, and 7 plausible, well-formed, *invented* Samsung instructions of
#: the kind a model produces from memory:
#:
#:     threshold   real steps kept   invented wrongly kept
#:       0.50         83/83               4/7
#:       0.55         83/83               1/7
#:       0.60         83/83               1/7
#:       0.65         83/83               0/7      <- lowest value with zero leaks
#:       0.80         83/83               0/7
#:
#: 0.65 is chosen as the *lowest* clean value, which leaves the most room for a legitimate
#: paraphrase. Being honest about what that table proves: all 83 real steps are verbatim
#: copies, so they pass on containment and say nothing about the paraphrase tier. The only
#: real evidence here is the leak count, so picking anything stricter than 0.65 would be
#: trading unmeasured paraphrase tolerance for margin we have no evidence we need.
#:
#: The one case that leaks at 0.60 is "Factory reset the device from General management."
#: against the touchscreen document -- which does discuss a factory data reset, so the
#: overlap is real even though the instruction was invented.
OVERLAP_THRESHOLD = 0.65

#: Too common to carry evidence of provenance. A step sharing only these with a source
#: line shares nothing meaningful.
_FILLER = frozenset({
    "the", "a", "an", "to", "and", "or", "of", "on", "in", "at", "for", "from", "with",
    "your", "you", "it", "its", "is", "are", "be", "then", "this", "that", "will", "can",
    "if", "up", "down", "out", "into", "select", "tap", "open", "go", "press", "hold",
})

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class GroundingReport:
    """What survived, what did not, and why -- so a caller can log a real reason."""

    kept: int = 0
    dropped: list[str] = field(default_factory=list)
    dropped_groups: int = 0
    dropped_actions: int = 0
    dropped_goals: int = 0

    @property
    def everything_dropped(self) -> bool:
        return self.kept == 0

    def summary(self) -> str:
        return (
            f"kept {self.kept} step(s), dropped {len(self.dropped)}, "
            f"{self.dropped_groups} group(s), {self.dropped_actions} action(s), "
            f"{self.dropped_goals} goal(s)"
        )


def normalise(text: str) -> str:
    return " ".join((text or "").lower().split())


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _FILLER and len(w) > 2}


def source_lines(content: str) -> list[str]:
    """Document lines usable as evidence, plus sentence-level splits.

    Sentences are included as well as lines because SIIS content packs several
    instructions onto one line, and a step legitimately lifted from the middle of such a
    line should still be recognised.
    """
    out: list[str] = []
    for raw in (content or "").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            sentence = sentence.strip()
            if sentence and sentence != line:
                out.append(sentence)
    return out


def is_grounded(step: str, content: str, threshold: float = OVERLAP_THRESHOLD) -> bool:
    """Can this step be traced back to the supplied document?"""
    if not isinstance(step, str) or not step.strip():
        return False
    if not content or not content.strip():
        return False

    normalised_step = normalise(step)
    normalised_content = normalise(content)
    if normalised_step in normalised_content:
        return True

    step_tokens = _tokens(step)
    if not step_tokens:
        # Nothing distinctive to verify. Containment already failed, so this is filler
        # dressed up as an instruction.
        return False

    for line in source_lines(content):
        line_tokens = _tokens(line)
        if not line_tokens:
            continue
        overlap = len(step_tokens & line_tokens) / len(step_tokens)
        if overlap >= threshold:
            return True
    return False


def ground_response(
    payload: dict[str, Any], content: str, threshold: float = OVERLAP_THRESHOLD
) -> tuple[dict[str, Any], GroundingReport]:
    """Strip every step that cannot be traced to ``content``.

    Empty step groups, then actions, then goals are pruned as they empty out. If the whole
    thing empties, the caller falls back -- that is the signal the model answered from
    general knowledge rather than from the document.
    """
    report = GroundingReport()
    if not isinstance(payload, dict):
        return {"contexts": []}, report

    goals_out = []
    for goal in payload.get("contexts") or []:
        if not isinstance(goal, dict):
            continue
        actions_out = []
        for action in goal.get("actions") or []:
            if not isinstance(action, dict):
                continue
            groups_out = []
            for group in action.get("stepGroups") or []:
                if not isinstance(group, dict):
                    continue
                steps_out = []
                for step in group.get("steps") or []:
                    if is_grounded(step, content, threshold):
                        steps_out.append(step)
                        report.kept += 1
                    else:
                        report.dropped.append(str(step)[:120])
                if steps_out:
                    groups_out.append({**group, "steps": steps_out})
                else:
                    report.dropped_groups += 1
            if groups_out:
                actions_out.append({**action, "stepGroups": groups_out})
            else:
                report.dropped_actions += 1
        if actions_out:
            goals_out.append({**goal, "actions": actions_out})
        else:
            report.dropped_goals += 1

    return {**payload, "contexts": goals_out}, report
