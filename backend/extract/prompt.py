"""The prompt. Every rule is stated here, not bolted on afterwards.

The rules are also enforced after the fact by ``grounding``, ``sanitize`` and ``validate``,
and that is deliberate belt-and-braces rather than duplication: putting a rule in the
prompt raises the chance of getting it right first time, which saves a retry and therefore
latency, while the post-checks are what make it *guaranteed*. The model's formatting is
never trusted on faith.

One rule is absent on purpose: the model is never asked for a ``bixby://`` URI, or told
that such a thing exists. It picks a numbered shortcut or answers ``none``. See
``candidates.py``.
"""

from __future__ import annotations

from typing import Sequence

from .candidates import SectionCandidates, render_for_prompt

#: The exact output contract. Kept separate from the instructions so it can be shown twice
#: -- models follow a schema far more reliably when it is restated next to the example.
OUTPUT_SHAPE = """{
  "goals": [
    {
      "name": "<one or two words naming the problem area, e.g. Screen Damage>",
      "title": "<exactly 2 or 3 words>",
      "score": <number between 0.0 and 1.0>,
      "actions": [
        {
          "actionName": "<2 to 4 words, Title Case>",
          "description": "<exactly 5 to 7 words, MUST begin with: It will>",
          "category": "auto" | "manual" | "critical",
          "section": <the SECTION number these steps came from>,
          "shortcut": <the number of one listed shortcut, or null>,
          "steps": ["<a line copied from that section>", "..."]
        }
      ]
    }
  ]
}"""

_INSTRUCTIONS = """You convert a Samsung support document into a structured repair plan.

You will be given the user's complaint and a support document split into numbered
SECTIONS. Each section lists the lines you may use, and any settings shortcuts available
for it.

HARD RULES. Breaking any of these makes the answer useless:

1. Return JSON only. No prose, no markdown, no code fences.
2. Every entry in "steps" MUST be a line copied from the SECTION you name in "section",
   word for word, or a very close paraphrase of one. Never write an instruction that is
   not in the document. Never use what you know about Samsung devices from elsewhere. If
   the document does not say it, it does not go in.
3. Never write a URL, a web address, an email address, or a link of any kind.
4. "shortcut" is either the NUMBER of one shortcut listed for that section, or null.
   Choose a shortcut only when it genuinely performs what the steps describe, including
   the right direction: if the steps say to turn a setting OFF, do not pick a shortcut
   that turns it ON. When nothing listed fits, answer null. Answering null is correct and
   expected; most sections have no suitable shortcut.
5. "category" rules:
     "auto"     only when you chose a shortcut number
     "critical" for anything that erases data or is irreversible
     "manual"   everything else, including every action where shortcut is null
6. "description" must be 5 to 7 words and must start with "It will". Count the words.
7. "title" must be exactly 2 or 3 words.
8. If the complaint contains SEVERAL DISTINCT problems, return a separate goal for each
   one. A complaint listing a cracked screen, dead touch areas and a dim display is three
   goals, not one.
9. "score" is your honest confidence that this plan addresses the complaint. If the
   document is only loosely related to what the user described, say so with a low score
   rather than inflating it.
10. Prefer fewer, well-grounded actions over many thin ones. Two to four actions per goal.

The document may not match the complaint well. That is normal and expected. Work from the
document you were given anyway -- describe what it actually says, and set a low score.
Do not substitute advice from your own knowledge."""


def build_prompt(
    query: str,
    siis_title: str,
    sections: Sequence[SectionCandidates],
    previous_errors: Sequence[str] = (),
) -> str:
    """Assemble the prompt.

    ``previous_errors`` carries ``validate()``'s own messages back into a retry. They
    already name the exact rule and the exact path (``contexts[0].actions[1].description
    must be 5-7 words, got 9``), which is far more useful to the model than a generic
    "try again" -- and it costs nothing to reuse them.
    """
    parts = [
        _INSTRUCTIONS,
        "",
        "OUTPUT SHAPE -- return exactly this structure:",
        OUTPUT_SHAPE,
        "",
        f"USER COMPLAINT:\n{(query or '').strip() or '(not supplied)'}",
        "",
        f"DOCUMENT TITLE:\n{(siis_title or '').strip() or '(untitled)'}",
        "",
        "DOCUMENT SECTIONS:",
        render_for_prompt(sections) or "(no sections could be read from the document)",
    ]

    if previous_errors:
        listed = "\n".join(f"  - {error}" for error in previous_errors[:12])
        parts += [
            "",
            "YOUR PREVIOUS ANSWER WAS REJECTED. Fix exactly these problems and return the "
            "whole JSON document again:",
            listed,
        ]

    parts += ["", "Return the JSON now."]
    return "\n".join(parts)
