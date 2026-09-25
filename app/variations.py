"""Query paraphrases. Owner: M4 (docs/PLAN.md, "Who owns what").

variations(query) -> [str], 8 to 10 of them

Genuinely diverse paraphrases — different vocabulary, sentence structure,
formality — not ten ways of saying the same sentence with the same words.
The count itself is scored under A5: not 7, not 11.
"""


def variations(query: str) -> list[str]:
    raise NotImplementedError(
        "M4: generate 8-10 diverse paraphrases, see docstring"
    )
