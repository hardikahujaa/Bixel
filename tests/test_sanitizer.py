"""Tests for sanitize(). Owner: M2.

Gate G5 is zero URLs anywhere in the output, and missing any gate zeroes the entire
automated score. The tests that matter most are the ones using the kit's real leak text,
because that text is run-together (``samsung.comusingyour...``) and defeats a naive
word-boundary pattern -- which is exactly the bug an earlier version of the detector had.
"""

import json

import pytest

from app.sanitizer import assert_url_free, sanitize, sanitize_response
from backend.matcher.catalog import contains_url

LEAK_ROWS = ("row_3", "row_11", "row_17")


@pytest.fixture(scope="module")
def siis_rows():
    payload = json.loads(open("student_kit/siis_responses.json", encoding="utf-8").read())
    return {row["id"]: row["siis_response"]["content"] for row in payload["responses"]}


# ------------------------------------------------------------------- the real leak -----


def test_the_three_leak_rows_are_url_free_after_sanitising(siis_rows):
    """row_3/11/17 contain kidshome.pin@samsung.com despite the kit claiming otherwise."""
    for row_id in LEAK_ROWS:
        raw = siis_rows[row_id]
        assert contains_url(raw), f"{row_id} was expected to contain a URL before sanitising"
        assert not contains_url(sanitize(raw)), f"{row_id} still has a URL after sanitising"


def test_the_other_rows_are_left_effectively_unchanged(siis_rows):
    """Sanitising a clean row must not quietly rewrite it.

    Whitespace is normalised, so compare on collapsed whitespace rather than byte equality.
    """
    for row_id, content in siis_rows.items():
        if row_id in LEAK_ROWS:
            continue
        assert sanitize(content) == " ".join(content.split()), f"{row_id} was altered"


def test_run_together_text_does_not_lose_words(siis_rows):
    """Only the email is removed, not the prose fused to it."""
    cleaned = sanitize(siis_rows["row_3"])
    assert "kidshome" not in cleaned
    assert "samsung.com" not in cleaned
    # the words fused onto the end of the address survive
    assert "usingyourregisteredemailaddress" in cleaned.replace(" ", "")


# --------------------------------------------------------------- what must survive -----


@pytest.mark.parametrize(
    "uri",
    [
        "bixby://masked/act/aa73a35e8d",
        "bixby://masked/val/ef6814259a",
        "bixby://dummy_positive",
    ],
)
def test_bixby_uris_are_never_stripped(uri):
    """These are what we are supposed to emit. Stripping one breaks A2 exact-match."""
    assert sanitize(uri) == uri
    assert not contains_url(uri)


@pytest.mark.parametrize(
    "text",
    [
        "Navigate to Settings, tap Display.",
        "One UI 6.1 or later",
        "Press and hold for 1.5 seconds",
        "e.g. the Power button",
        "version 2.4.3",
        "Tap Apps, then Storage, then Clear cache.",
        "Your device's Edge panel lets you access apps.",
    ],
)
def test_ordinary_prose_is_untouched(text):
    assert sanitize(text) == text


# ----------------------------------------------------------------- what must go -----


@pytest.mark.parametrize(
    "dirty",
    [
        "Visit samsung.com for more information.",
        "Go to https://example.org/help now",
        "See www.samsung.com/support",
        "Read [the guide](http://x.io) first",
        "Email kidshome.pin@samsung.com to reset",
        "Open support.samsung.com in a browser",
        "The page is named troubleshooting.html",
    ],
)
def test_url_shaped_text_is_removed(dirty):
    cleaned = sanitize(dirty)
    assert not contains_url(cleaned), f"survived: {cleaned!r}"
    assert cleaned == " ".join(cleaned.split()), "left ragged whitespace behind"


# ------------------------------------------------------------------- robustness -----


def test_none_and_empty_are_safe():
    assert sanitize(None) == ""
    assert sanitize("") == ""
    assert sanitize("   \t\n ") == ""


@pytest.mark.parametrize("bad", [123, [], {}, 4.5, object()])
def test_non_string_raises(bad):
    """A dict reaching sanitize() is a wiring bug. Better to surface it than to emit a repr."""
    with pytest.raises(TypeError):
        sanitize(bad)


# ------------------------------------------------------- whole-response sanitising -----


def test_sanitize_response_walks_nesting_and_preserves_deeplinks():
    payload = {
        "contexts": [
            {
                "goal": "Visit samsung.com for this Screen Troubleshooting.",
                "actions": [
                    {
                        "description": "It will open support.samsung.com",
                        "stepGroups": [
                            {
                                "steps": ["Go to www.samsung.com", "Tap Display."],
                                "actionableDeeplink": {
                                    "deeplink": "bixby://masked/act/aa73a35e8d",
                                    "description": "Opens the settings page.",
                                },
                            }
                        ],
                    }
                ],
                "score": 0.9,
            }
        ]
    }
    cleaned = sanitize_response(payload)

    group = cleaned["contexts"][0]["actions"][0]["stepGroups"][0]
    assert group["actionableDeeplink"]["deeplink"] == "bixby://masked/act/aa73a35e8d"
    assert "samsung.com" not in json.dumps(cleaned).replace("bixby://", "")
    assert cleaned["contexts"][0]["score"] == 0.9, "non-string values must pass through"
    assert group["steps"][1] == "Tap Display."


def test_assert_url_free_accepts_a_clean_response():
    for name in ("normal", "dummy_positive", "critical"):
        payload = json.loads(open(f"fixtures/{name}.json", encoding="utf-8").read())
        assert_url_free(payload, name)  # must not raise


def test_assert_url_free_raises_on_a_leak():
    with pytest.raises(ValueError, match="URL-shaped text survived"):
        assert_url_free({"contexts": [{"goal": "see samsung.com"}]})


def test_assert_url_free_ignores_deeplink_fields():
    assert_url_free({"actionableDeeplink": {"deeplink": "bixby://masked/act/aa73a35e8d"}})
