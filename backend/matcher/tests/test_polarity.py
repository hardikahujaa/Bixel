"""Tests for on/off direction resolution.

This matters because 111 settings exist as both Enable and Disable with an *identical*
``qna_description``, so the embedding cannot tell them apart and the direction has to
come from the step text.
"""

from __future__ import annotations

import pytest

from backend.matcher.polarity import polarity_agrees, resolve_polarity


@pytest.mark.parametrize(
    "text,expected",
    [
        ("To turn off this feature, navigate to Settings.", -1),
        ("You can disable the full screen gesture function.", -1),
        ("Deactivate Super steady mode.", -1),
        ("Uncheck the box to stop the behaviour.", -1),
        ("Turn on Touch sensitivity for better response.", 1),
        ("Enable Multi window for all apps.", 1),
        ("Activate the Edge panel.", 1),
        ("Make sure auto rotate is on.", 1),
    ],
)
def test_strong_cues(text, expected):
    assert resolve_polarity(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Navigate to Settings, tap Display.",
        "Tap the icon on the screen.",
        "Place the devices on a flat surface.",
        "You can use Multi window on your Galaxy foldable devices.",
        "",
        "   ",
    ],
)
def test_no_cue_is_undecided(text):
    """A bare 'on' in ordinary prose is not a direction.

    "Tap the icon on the screen" must not read as Enable, or every third step would
    get an arbitrary polarity.
    """
    assert resolve_polarity(text) == 0


def test_real_touch_sensitivity_text_resolves_to_disable():
    """The actual SIIS wording, which contains BOTH directions.

    "If the Touch sensitivity setting is enabled ... To turn off this feature" -- the
    instruction is the later clause, so the answer is Disable. Getting this backwards
    returns DL-0126 instead of DL-0125, which is a wrong deeplink that looks right.
    """
    text = (
        "If the Touch sensitivity setting is enabled when you are not using a "
        "protective film, the touchscreen might become overly sensitive and "
        "malfunction. To turn off this feature, navigate to Settings, tap Display, "
        "and then tap the switch next to Touch sensitivity."
    )
    assert resolve_polarity(text) == -1


def test_conflicting_cues_prefer_the_later_instruction():
    assert resolve_polarity("Disable it first, then enable it again.") == 1
    assert resolve_polarity("Enable it first, then disable it again.") == -1


def test_none_and_non_string_do_not_raise():
    assert resolve_polarity(None) == 0  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "step,entry,agrees",
    [
        (1, 1, True),
        (-1, -1, True),
        (1, -1, False),
        (-1, 1, False),
        (0, 1, True),      # undecided step: keep both halves
        (0, -1, True),
        (1, 0, True),      # neutral entry has no direction to contradict
        (-1, 0, True),
        (0, 0, True),
    ],
)
def test_polarity_agrees(step, entry, agrees):
    assert polarity_agrees(step, entry) is agrees
