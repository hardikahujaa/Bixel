"""LLM extraction pipeline. Owner: M1 (docs/PLAN.md, "Who owns what").

extract(query, siis_response) -> ContextDeeplinkResponse

Turns a SIIS {title, content} payload plus the user's query into goals ->
actions -> step groups. Steps must be lifted from the supplied content,
never invented. The model emits a catalog_id or null per step group, never
a raw URI -- that is what makes a hallucinated deeplink structurally
impossible instead of something caught after the fact.

This module is deliberately a thin delegation. The implementation lives in
``backend/extract/`` -- pipeline, prompt, candidate builder, grounding check and
deterministic fallback are separate concerns and each has its own tests. Keeping the
import path here means M3's wiring does not change.

For an API-level test that needs no key and no network, pass a client:

    from backend.extract.client import FakeClient
    extract(query, siis, client=FakeClient('{"goals": [...]}'))
"""
from typing import Any

from student_kit.schema import ContextDeeplinkResponse

from backend.extract.pipeline import extract as _extract


def extract(query: str, siis_response: dict, **kwargs: Any) -> ContextDeeplinkResponse:
    """See ``backend.extract.pipeline.extract``.

    ``**kwargs`` forwards the optional ``client``, ``matcher`` and ``trace`` injection
    points without putting them in the agreed two-argument signature.
    """
    return _extract(query, siis_response, **kwargs)
