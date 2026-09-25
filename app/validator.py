"""Response validator. Owner: M2 (docs/PLAN.md, "Who owns what").

validate(response) -> {"ok": bool, "errors": [str]}

Checks every formatting rule in docs/KIT_NOTES.md section 8 ("The formatting rules, in one place") that schema.py itself
does not enforce: goal regex + trailing period, title is 2-3 words, every
action description is 5-7 words and starts with "It will", score in
[0.0, 1.0], every steps list non-empty, category is present (not silently
defaulted), every "auto" action carries an actionableDeeplink, and a URL/URI
scan against the two catalog allowlists (act URIs vs val URIs — zero
overlap between them).

First acceptance test: this must REJECT student_kit/sample_output.json —
its two Action.description values are 9 and 12 words, over the 5-7 word
limit. If validate() passes that file, validate() is wrong.
"""
from typing import Any


def validate(response: dict) -> dict[str, Any]:
    raise NotImplementedError(
        "M2: implement every section 3/6 rule; see docstring for the acceptance test"
    )
