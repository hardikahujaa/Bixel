"""FastAPI service entrypoint. Owner: M3 (docs/PLAN.md, "Who owns what").

Run from the repo root: python -m uvicorn app.main:app --reload
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from app.cache import Cache
from app.extractor import extract
from backend.extract.client import LLMClient
from backend.extract.pipeline import ExtractionTrace
from backend.matcher.matcher import get_matcher
from student_kit.schema import ContextDeeplinkResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The embedding model's ONNX session initialises lazily on first inference
    # (~1s -- backend/matcher/embedder.py). Both the matcher and the cache
    # route through that same model, so one dummy match() call here warms it
    # for both before the first real request has to pay that cost.
    get_matcher().match("warm up the embedding model")
    yield


app = FastAPI(title="Bixel", lifespan=lifespan)

#: Process-wide, like the matcher singleton it shares an embedding model with.
_cache = Cache()


class SiisResponsePayload(BaseModel):
    title: str
    content: str


class TroubleshootRequest(BaseModel):
    query: str
    siis_response: SiisResponsePayload


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def get_llm_client() -> LLMClient | None:
    """Dependency seam, not a config option.

    Production always gets None here, which tells extract() to build its own
    default (real) Gemini client. Tests override this (tests/conftest.py)
    with a FakeClient so the default suite needs no key and no network --
    the same goal backend/extract/tests/conftest.py states for the pipeline's
    own tests, applied here at the API layer, which is exactly what
    FakeClient's docstring says it exists for (backend/extract/client.py).
    """
    return None


@app.post("/v1/troubleshoot", response_model=ContextDeeplinkResponse)
def troubleshoot(
    request: TroubleshootRequest,
    llm_client: LLMClient | None = Depends(get_llm_client),
) -> ContextDeeplinkResponse:
    """extract() -> cached.

    extract() already performs the to_deeplink_pair -> sanitize -> validate
    assembly internally (backend/extract/pipeline.py) and never returns an
    empty response, so the wiring here is the cache: a paraphrase of an
    already-served query, against the same SIIS content, returns the stored
    answer instead of paying for a fresh model call.

    Only a real model answer is stored (``trace.route == "model"``). A
    request that took any fallback path -- a Gemini 503, a timeout, an
    ungrounded answer -- gets a real, valid response today, but it is never
    written into the cache: caching it would serve that degraded answer to
    every later paraphrase of the same query even after Gemini recovers,
    instead of trying again (app/cache.py's ``store_if``).
    """
    siis = request.siis_response.model_dump()
    trace = ExtractionTrace()

    def compute() -> ContextDeeplinkResponse:
        return extract(request.query, siis, client=llm_client, trace=trace)

    return _cache.get_or_compute(
        request.query, siis, compute, store_if=lambda _: trace.route == "model"
    )
