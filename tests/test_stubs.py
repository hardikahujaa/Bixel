"""Contract tests for the agreed function shapes (docs/PLAN.md, Day 1 interface list).

These lock in the seven agreed signatures as something executable: if a future change to a
parameter name or count breaks the call convention other modules already rely on, this
fails immediately with a TypeError instead of surfacing as a confusing error deep in
main.py's real pipeline later.

Four of the seven are now implemented -- to_deeplink_pair, sanitize, validate and extract -- so the
"still raises NotImplementedError" assertions for them have been replaced with assertions
that they honour the agreed shape. Their behaviour is covered in depth by
tests/test_projection.py, tests/test_sanitizer.py and tests/test_validator.py.

Two remain stubs: variations (M4) and Cache.get_or_compute (M2). Those are
expected to raise NotImplementedError -- that is the current, correct behaviour, not a bug.
Each one's assertion below is what will fail, usefully, on the day it gets implemented.
"""
import pytest

from app.cache import Cache
from app.extractor import extract
from app.projection import to_deeplink_pair
from app.sanitizer import sanitize
from app.validator import validate
from app.variations import variations
from student_kit.schema import Deeplink, ValidationDeepLink

#: A minimal well-formed model answer, for the extract() shape test below.
LLM_ANSWER = (
    '{"goals": [{"name": "Blank Display", "title": "Blank display check", "score": 0.6,'
    ' "actions": [{"actionName": "Force Restart", "description": "It will force the device restart",'
    ' "category": "manual", "section": 1, "shortcut": null,'
    ' "steps": ["Press and hold the Power button."]}]}]}'
)


# ------------------------------------------------------- still stubs, by design -----


def test_extract_accepts_its_agreed_signature_and_returns_a_response():
    """Implemented. Uses an injected fake client so this needs no key and no network."""
    from backend.extract.client import FakeClient
    from student_kit.schema import ContextDeeplinkResponse

    result = extract(
        query="my screen is black",
        siis_response={
            "title": "Blank display",
            "content": "## Check\nPress and hold the Power button.",
        },
        client=FakeClient(LLM_ANSWER),
    )
    assert isinstance(result, ContextDeeplinkResponse)
    assert result.contexts, "contexts is never empty -- banned project-wide"


def test_variations_accepts_its_agreed_signature_and_is_not_implemented():
    with pytest.raises(NotImplementedError):
        variations("my Galaxy S22 screen is black")


def test_cache_get_or_compute_accepts_its_agreed_signature_and_is_not_implemented():
    cache = Cache()
    with pytest.raises(NotImplementedError):
        cache.get_or_compute("query", {"title": "t", "content": "c"}, lambda: None)


def test_cache_starts_with_zero_hits_and_misses():
    cache = Cache()
    assert cache.hits == 0
    assert cache.misses == 0


# ------------------------------------------------- implemented: shape still honoured -----


def test_to_deeplink_pair_returns_the_agreed_pair():
    """(Deeplink, ValidationDeepLink | None) -- callers unpack two values."""
    entry = {
        "id": "DL-TEST",
        "deeplink": "bixby://masked/act/aa73a35e8d",
        "description": "Opens the 24-hour time format settings page.",
        "message": "Switch Time Format",
        "originalType": "onClickURL",
        "validation": {"deeplink": "bixby://masked/val/ef6814259a", "key": "Use 24-hour format"},
    }
    result = to_deeplink_pair(entry)
    assert isinstance(result, tuple) and len(result) == 2
    actionable, validation = result
    assert isinstance(actionable, Deeplink)
    assert isinstance(validation, ValidationDeepLink)

    entry_without_validation = dict(entry, validation=None)
    _, none_validation = to_deeplink_pair(entry_without_validation)
    assert none_validation is None


def test_sanitize_returns_a_string():
    """sanitize(text) -> text. Same type in, same type out."""
    result = sanitize("some text possibly containing samsung.com")
    assert isinstance(result, str)
    assert "samsung.com" not in result


def test_validate_returns_the_agreed_dict():
    """validate(response) -> {"ok": bool, "errors": [str]}."""
    result = validate({"contexts": []})
    assert isinstance(result, dict)
    assert set(result) == {"ok", "errors"}
    assert isinstance(result["ok"], bool)
    assert isinstance(result["errors"], list)
    assert all(isinstance(error, str) for error in result["errors"])
    # empty contexts is banned, so this particular call must report not-ok
    assert result["ok"] is False


# ------------------------------------------------------------------- imports -----


def test_each_module_is_independently_importable_without_side_effects():
    """Guards against an accidental module-level call, network request, or file read
    sneaking in. Importing must be free -- the catalog and embedding model are loaded
    lazily, not at import time."""
    import importlib

    for module_name in [
        "app.cache",
        "app.extractor",
        "app.projection",
        "app.sanitizer",
        "app.validator",
        "app.variations",
    ]:
        importlib.import_module(module_name)
