"""Shared fixtures for the top-level test suite.

Overrides /v1/troubleshoot's LLM client with a scripted FakeClient so the
default suite -- what a judge or CI runs with no GEMINI_API_KEY -- never
calls the real Gemini API. Mirrors backend/extract/tests/conftest.py's same
goal for the extraction pipeline's own unit tests ("the suite a judge runs
must not need an API key"), extended to the API layer now that app/main.py
wires extract() in for real instead of returning a fixed placeholder.
"""
import pytest

from app.main import _cache, app, get_llm_client
from backend.extract.client import FakeClient

#: A well-formed answer for requests whose SIIS content is rich enough to
#: reach the model route at all. Most of this suite's payloads are too thin
#: to ground anything against, so the deterministic fallback answers those
#: regardless of what is scripted here.
_SCRIPTED_ANSWER = (
    '{"goals": [{"name": "Blank Display", "title": "Blank display check", "score": 0.6,'
    ' "actions": [{"actionName": "Force Restart", "description": "It will force the device restart",'
    ' "category": "manual", "section": 1, "shortcut": null,'
    ' "steps": ["Press and hold the Power button."]}]}]}'
)


@pytest.fixture(autouse=True)
def _no_live_llm_calls():
    app.dependency_overrides[get_llm_client] = lambda: FakeClient(_SCRIPTED_ANSWER)
    yield
    app.dependency_overrides.pop(get_llm_client, None)


@pytest.fixture(autouse=True)
def _reset_troubleshoot_cache():
    """``_cache`` is a process-wide singleton in app/main.py, same as it will
    be in production -- but that means it persists across test functions
    too. Without this, one test's cached answer would silently satisfy
    another test's assertions instead of exercising the endpoint."""
    _cache.hits = 0
    _cache.misses = 0
    _cache._buckets.clear()
    yield
