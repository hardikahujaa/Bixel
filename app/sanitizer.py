"""Output sanitizer. Owner: M2 (docs/PLAN.md, "Who owns what").

sanitize(text) -> text

Strips URL-shaped tokens (http, www., .com, .html, markdown links) from any
string heading for the output. Exists because student_kit/siis_responses.json
row_3, row_11 and row_17 contain "samsung.com" despite the kit's own _readme
claiming the content is URL-free — one leak zeroes gate G5.
"""


def sanitize(text: str) -> str:
    raise NotImplementedError(
        "M2: strip URL-shaped tokens; test against rows 3, 11, 17"
    )
