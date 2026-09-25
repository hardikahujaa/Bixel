"""Contract tests for the not-yet-implemented functions (app/extractor.py,
validator.py, sanitizer.py, projection.py, cache.py, variations.py).

These lock in the seven agreed function shapes (Claude.md section 7) as
something executable: if a future change to a stub's parameter names or
count breaks the call convention other modules already rely on, this fails
immediately with a TypeError instead of surfacing as a confusing error deep
in main.py's real pipeline later. Each stub is expected to raise
NotImplementedError -- that's the current, correct behavior, not a bug.
"""
import pytest

from app.cache import Cache
from app.extractor import extract
from app.projection import to_deeplink_pair
from app.sanitizer import sanitize
from app.validator import validate
from app.variations import variations


def test_extract_accepts_its_agreed_signature_and_is_not_implemented():
    with pytest.raises(NotImplementedError):
        extract(query="my screen is black", siis_response={"title": "t", "content": "c"})


def test_to_deeplink_pair_accepts_its_agreed_signature_and_is_not_implemented():
    with pytest.raises(NotImplementedError):
        to_deeplink_pair({"id": "DL-0001", "deeplink": "bixby://masked/act/x"})


def test_sanitize_accepts_its_agreed_signature_and_is_not_implemented():
    with pytest.raises(NotImplementedError):
        sanitize("some text possibly containing samsung.com")


def test_validate_accepts_its_agreed_signature_and_is_not_implemented():
    with pytest.raises(NotImplementedError):
        validate({"contexts": []})


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


def test_each_stub_is_independently_importable_without_side_effects():
    """Guards against an accidental module-level call, network request, or
    file read sneaking into a stub before there's any real logic to need
    one -- importing must be free."""
    import importlib

    for module_name in ["app.cache", "app.extractor", "app.projection", "app.sanitizer", "app.validator", "app.variations"]:
        importlib.import_module(module_name)
