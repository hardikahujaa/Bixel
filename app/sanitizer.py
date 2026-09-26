"""Output sanitizer. Owner: M2 (docs/PLAN.md, "Who owns what").

sanitize(text) -> text

Strips URL-shaped tokens (http, www., .com, .html, markdown links, email addresses) from
any string heading for the output. Exists because student_kit/siis_responses.json row_3,
row_11 and row_17 contain "samsung.com" despite the kit's own _readme claiming the
content is URL-free -- one leak zeroes gate G5, and G5 failing zeroes the entire
automated score.

Detection is **not** reimplemented here. It reuses the canonical patterns in
``backend.matcher.catalog``, so there is exactly one definition of "URL-shaped" in the
project. That matters: an earlier version of that detector missed the real leak because
the kit writes it run-together as
``kidshome.pin@samsung.comusingyourregisteredemailaddress`` with no space after "com".

Two things this must never do:

* **Strip a ``bixby://`` URI.** Those are what we are supposed to emit. Only http/https
  schemes, www hosts, markdown links, emails and bare lowercase domains are removed.
* **Mangle ordinary prose.** "One UI 6.1", "version 2.4.3" and "e.g. the Power button"
  all survive untouched.
"""
from typing import Any

from backend.matcher.catalog import URL_PATTERNS, contains_url


def sanitize(text: str) -> str:
    """Remove every URL-shaped token from ``text`` and tidy the resulting whitespace.

    ``None`` is treated as empty text rather than raising, because this sits in a live
    response path. Any other non-string is a programming error and raises, so it surfaces
    in tests instead of silently emitting a dict repr into a response.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        raise TypeError(f"sanitize() expects str or None, got {type(text).__name__}")
    if not text:
        return ""

    cleaned = text
    for pattern in URL_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)

    # Collapse the whitespace the removals left behind, and clean up punctuation that is
    # now stranded ("Visit  . For more" -> "Visit. For more").
    cleaned = " ".join(cleaned.split())
    for stranded, fixed in ((" .", "."), (" ,", ","), (" :", ":"), (" ;", ";"), ("( )", "")):
        cleaned = cleaned.replace(stranded, fixed)
    return " ".join(cleaned.split())


def sanitize_response(payload: Any) -> Any:
    """Walk a response structure and sanitize every string in it.

    Additive helper, not part of the agreed seven signatures -- it is here so the wiring
    in ``app/main.py`` has one call to make at the output boundary instead of hand-walking
    the nesting. Dict keys are left alone; only values are sanitized.

    Note the asymmetry with :func:`sanitize`: ``deeplink`` values are passed through
    untouched. They are catalog URIs copied verbatim, the catalog is verified URL-free,
    and rewriting one would break the exact-match requirement that A2 scores.
    """
    if isinstance(payload, str):
        return sanitize(payload)
    if isinstance(payload, dict):
        return {
            key: value if key == "deeplink" else sanitize_response(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [sanitize_response(item) for item in payload]
    return payload


def assert_url_free(payload: Any, where: str = "response") -> None:
    """Raise if any string anywhere in ``payload`` still looks like a URL.

    The last line of defence before something leaves the service. Intended to run after
    :func:`sanitize_response`, so a failure here means the sanitizer has a gap rather
    than that the input was dirty.
    """
    for path, value in _walk_strings(payload, where):
        if path.endswith("deeplink"):
            continue
        if contains_url(value):
            raise ValueError(f"URL-shaped text survived sanitising at {path}: {value!r}")


def _walk_strings(payload: Any, path: str):
    if isinstance(payload, str):
        yield path, payload
    elif isinstance(payload, dict):
        for key, value in payload.items():
            yield from _walk_strings(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            yield from _walk_strings(item, f"{path}[{index}]")
