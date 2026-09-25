"""Tests for the catalog loader, asserted against the real student_kit catalog.

These are deliberately concrete. Every count below was measured from
``student_kit/deeplinks.json``, so if Samsung ever ships a different catalog these
tests fail loudly instead of the matcher silently degrading.
"""

from __future__ import annotations

import pytest

from backend.matcher.catalog import (
    DUMMY_DEEPLINK,
    actionable_uris,
    build_blob,
    contains_url,
    grounding_terms,
    load_catalog,
    polarity_pairs,
    split_message,
    validation_uris,
)

EXPECTED_ENTRY_COUNT = 578
EXPECTED_POLARITY_PAIRS = 111
EXPECTED_VALIDATION_URIS = 419


@pytest.fixture(scope="module")
def entries():
    return load_catalog()


def test_loads_every_entry(entries):
    assert len(entries) == EXPECTED_ENTRY_COUNT


def test_ids_and_uris_are_unique(entries):
    assert len({e.id for e in entries}) == EXPECTED_ENTRY_COUNT
    assert len({e.deeplink for e in entries}) == EXPECTED_ENTRY_COUNT


def test_all_blobs_non_empty(entries):
    """Guards the 10 entries with an empty qna_description.

    An empty blob would embed to a meaningless vector that could match anything.
    """
    empty = [e.id for e in entries if not e.blob.strip()]
    assert empty == []


def test_entries_with_empty_qna_still_get_a_blob(entries):
    """The appliance/diagnostic entries fall back to message + description."""
    no_qna = [e for e in entries if not e.qna_description]
    assert len(no_qna) == 10, "catalog changed: expected 10 entries with no qna_description"
    for entry in no_qna:
        assert entry.blob.strip()
        assert entry.description, f"{entry.id} has neither qna_description nor description"


def test_raw_entry_is_untouched(entries):
    """match_deeplinks hands `raw` to callers. Projection is M2's job, not ours."""
    for entry in entries[:50]:
        assert entry.raw["id"] == entry.id
        assert entry.raw["deeplink"] == entry.deeplink
        # we must not have added or removed keys
        assert set(entry.raw) == {
            "id",
            "deeplink",
            "description",
            "message",
            "originalType",
            "control_type",
            "qna_description",
            "validation",
        }


def test_catalog_contains_no_urls(entries):
    """G5 is a hard gate. The catalog README claims it is clean; verify it."""
    offenders = [
        (e.id, field)
        for e in entries
        for field in (e.message, e.description, e.qna_description, e.blob)
        if contains_url(field)
    ]
    assert offenders == []


def test_exactly_one_placeholder(entries):
    placeholders = [e for e in entries if e.is_placeholder]
    assert len(placeholders) == 1
    assert placeholders[0].deeplink == DUMMY_DEEPLINK
    assert placeholders[0].original_type == "placeholder"


def test_actionable_and_validation_uris_are_disjoint(entries):
    """Two allowlists, not one. act and val URIs never overlap."""
    act = actionable_uris(entries)
    val = validation_uris(entries)
    assert len(act) == EXPECTED_ENTRY_COUNT
    assert len(val) == EXPECTED_VALIDATION_URIS
    assert act & val == frozenset()


def test_polarity_pair_count(entries):
    """38% of the catalog is Enable/Disable twins -- the core matching hazard."""
    pairs = polarity_pairs(entries)
    assert len(pairs) == EXPECTED_POLARITY_PAIRS
    for name, halves in pairs.items():
        assert set(halves) == {1, -1}, f"{name} is not a complete pair"


def test_known_polarity_pair_resolves_both_ways(entries):
    pairs = polarity_pairs(entries)
    assert pairs["touch sensitivity"] == {1: "DL-0126", -1: "DL-0125"}


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Enable Touch sensitivity", ("touch sensitivity", 1)),
        ("Disable Touch sensitivity", ("touch sensitivity", -1)),
        ("View Reset Options", ("reset options", 0)),
        ("Adjust Screen zoom", ("screen zoom", 0)),
        ("Check Battery Performance", ("battery performance", 0)),
        ("Switch Time Format", ("time format", 0)),
        ("Offurl", ("offurl", 0)),
        ("", ("", 0)),
        ("   ", ("", 0)),
        ("Enable", ("enable", 0)),  # bare verb with no setting name
    ],
)
def test_split_message(message, expected):
    assert split_message(message) == expected


def test_grounding_terms_drop_boilerplate():
    terms = grounding_terms("touch sensitivity")
    assert terms == {"touch", "sensitivity"}
    # "settings" and "screen" are too generic to identify anything
    assert "settings" not in grounding_terms("update settings")
    assert "screen" not in grounding_terms("screen zoom")
    assert "zoom" in grounding_terms("screen zoom")


def test_every_entry_has_grounding_terms_or_is_known_odd(entries):
    """An entry with no distinctive terms can never be lexically grounded.

    Two entries have messages that are pure boilerplate artefacts ("Offurl"/"Onurl"),
    so they are allowed through as known-odd rather than silently accepted.
    """
    bare = sorted(e.id for e in entries if not e.grounding_terms)
    assert bare == [], f"entries with no grounding terms: {bare}"


def test_build_blob_prefers_qna_then_falls_back():
    assert build_blob({"id": "x", "qna_description": "intent", "message": "m", "description": "d"}).startswith("intent")
    assert build_blob({"id": "x", "qna_description": "", "message": "m", "description": "d"}) == "m | d"
    assert build_blob({"id": "x", "qna_description": "", "message": "", "description": "d"}) == "d"
    with pytest.raises(ValueError):
        build_blob({"id": "x", "qna_description": "", "message": "", "description": ""})


@pytest.mark.parametrize(
    "text",
    [
        "Visit samsung.com for help",
        "go to https://example.org",
        "see www.samsung.com",
        "read [here](http://x.io)",
        "open support.samsung.com now",
    ],
)
def test_contains_url_detects(text):
    assert contains_url(text)


@pytest.mark.parametrize(
    "text",
    ["Navigate to Settings.", "Tap Apps.", "", "Enable Touch sensitivity", "bixby://masked/act/aa73a35e8d"],
)
def test_contains_url_allows_clean_text(text):
    assert not contains_url(text)
