"""Contract tests for the agreed function shapes (docs/PLAN.md, Day 1 interface list).

These lock in the seven agreed signatures as something executable: if a future change to a
parameter name or count breaks the call convention other modules already rely on, this
fails immediately with a TypeError instead of surfacing as a confusing error deep in
main.py's real pipeline later.

All seven are now implemented -- to_deeplink_pair, sanitize, validate, extract,
Cache.get_or_compute and variations -- so every "still raises NotImplementedError"
assertion has been replaced with one that honours the agreed shape. Their behaviour is
covered in depth by tests/test_projection.py, tests/test_sanitizer.py,
tests/test_validator.py, tests/test_cache.py and tests/test_variations.py.
"""
from app.cache import Cache
from app.extractor import extract
from app.projection import to_deeplink_pair
from app.sanitizer import sanitize
from app.validator import validate
from app.variations import MAX_COUNT, MIN_COUNT, variations
from backend.extract.client import FakeClient, LLMUnavailable
from student_kit.schema import Deeplink, ValidationDeepLink

#: A minimal well-formed model answer, for the extract() shape test below.
LLM_ANSWER = (
    '{"goals": [{"name": "Blank Display", "title": "Blank display check", "score": 0.6,'
    ' "actions": [{"actionName": "Force Restart", "description": "It will force the device restart",'
    ' "category": "manual", "section": 1, "shortcut": null,'
    ' "steps": ["Press and hold the Power button."]}]}]}'
)


# ------------------------------------------------------------- implemented -----


def test_extract_accepts_its_agreed_signature_and_returns_a_response():
    """Uses an injected fake client so this needs no key and no network."""
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


def test_variations_accepts_its_agreed_signature_and_returns_8_to_10():
    """Uses a client that refuses, so this exercises the deterministic path --
    needs no key and no network. tests/test_variations.py covers both paths."""
    result = variations(
        "my Galaxy S22 screen is black",
        client=FakeClient(LLMUnavailable("simulated: no key")),
    )
    assert MIN_COUNT <= len(result) <= MAX_COUNT
    assert all(isinstance(item, str) for item in result)


def test_cache_get_or_compute_accepts_its_agreed_signature_and_computes_on_a_miss():
    cache = Cache()
    result = cache.get_or_compute("query", {"title": "t", "content": "c"}, lambda: "computed")
    assert result == "computed"
    assert cache.misses == 1


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
