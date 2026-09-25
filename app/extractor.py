"""LLM extraction pipeline. Owner: M1 (docs/PLAN.md, "Who owns what").

extract(query, siis_response) -> ContextDeeplinkResponse

Turns a SIIS {title, content} payload plus the user's query into goals ->
actions -> step groups. Steps must be lifted from the supplied content,
never invented. The model emits a catalog_id or null per step group, never
a raw URI — that is what makes a hallucinated deeplink structurally
impossible instead of something caught after the fact.
"""
from student_kit.schema import ContextDeeplinkResponse


def extract(query: str, siis_response: dict) -> ContextDeeplinkResponse:
    raise NotImplementedError(
        "M1: call Gemini, prompt-constrained to the supplied SIIS content"
    )
