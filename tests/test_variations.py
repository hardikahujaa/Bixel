"""Tests for app/variations.py -- variations(query) (docs/PLAN.md, Day 1
interface list, and A5's "8 to 10, not 7, not 11" scoring rule).

Every call here passes an explicit ``client`` -- never the real default --
so the suite never depends on a key or the network, the same convention
backend/extract/tests uses for extract() (see backend/extract/client.py's
FakeClient docstring: "so M3 can use it for API-level tests").
"""
import pytest

from app.variations import MAX_COUNT, MIN_COUNT, lexical_diversity, variations
from backend.extract.client import FakeClient, LLMUnavailable

#: Forces the LLM path to fail immediately, so variations() falls through to
#: the deterministic generator without ever touching the network.
_NO_LLM = FakeClient(LLMUnavailable("simulated: no key"))


def test_raises_on_empty_query():
    with pytest.raises(ValueError):
        variations("", client=_NO_LLM)
    with pytest.raises(ValueError):
        variations("   ", client=_NO_LLM)


# --------------------------------------------------------- deterministic path


def test_deterministic_path_returns_a_count_in_the_scored_range():
    result = variations("my Galaxy S22 screen is completely black", client=_NO_LLM)
    assert MIN_COUNT <= len(result) <= MAX_COUNT


def test_deterministic_path_returns_unique_non_empty_strings():
    result = variations("my Galaxy S22 screen is completely black", client=_NO_LLM)
    assert all(isinstance(item, str) and item.strip() for item in result)
    assert len(set(item.strip().lower() for item in result)) == len(result)


def test_deterministic_path_never_echoes_the_original_query_verbatim():
    query = "my Galaxy S22 screen is completely black"
    result = variations(query, client=_NO_LLM)
    assert query.strip().lower() not in {item.strip().lower() for item in result}


def test_deterministic_path_varies_structure_not_just_one_word():
    """Every variant should carry a different opening frame -- if it didn't,
    this would just be word substitution wearing a diversity costume."""
    result = variations("my wifi keeps dropping every few minutes", client=_NO_LLM)
    openers = {item.split(",")[0].split(":")[0].split("?")[0][:12] for item in result}
    assert len(openers) == len(result)


def test_deterministic_path_is_deterministic():
    a = variations("camera won't focus properly", client=_NO_LLM)
    b = variations("camera won't focus properly", client=_NO_LLM)
    assert a == b


def test_deterministic_path_strips_the_kits_own_list_numbering():
    """student_kit/siis_responses.json's original_query field carries a
    literal list-numbering prefix on some rows (docs/KIT_NOTES.md section 7)
    -- row_1 is "1. My Samsung..." and row_17 is '1. "My Galaxy S24...'.
    Left in, every paraphrase would visibly repeat "1." instead of varying."""
    plain = variations("1. My Samsung A115G tablet screen flashes and goes black", client=_NO_LLM)
    quoted = variations('1. "My Galaxy S24 screen goes completely blank."', client=_NO_LLM)

    assert all("1." not in item for item in plain)
    assert all("1." not in item and '"' not in item for item in quoted)


def test_deterministic_path_handles_a_query_with_no_known_vocabulary():
    """No word in this query is in the synonym table -- the template wrappers
    alone must still produce a valid, non-duplicated set."""
    result = variations("xyzzy plugh frobnicate", client=_NO_LLM)
    assert MIN_COUNT <= len(result) <= MAX_COUNT
    assert len(set(result)) == len(result)


# ---------------------------------------------------------------- LLM path


def _scripted(paraphrases) -> FakeClient:
    import json

    return FakeClient(json.dumps(paraphrases))


_GOOD_PARAPHRASES = [
    "my Galaxy S22 screen is completely black",
    "S22 display won't turn on at all",
    "phone powers up but nothing shows on screen",
    "the display is totally dark on my device",
    "why won't my screen light up",
    "screen stays pitch black no matter what I do",
    "device boots but the display never comes on",
    "my phone's screen shows nothing whatsoever",
    "blank screen, device otherwise seems to be running",
]


def test_llm_path_is_used_when_it_returns_a_valid_diverse_answer():
    result = variations("my S22's screen won't display anything", client=_scripted(_GOOD_PARAPHRASES))
    assert result == _GOOD_PARAPHRASES


def test_llm_path_falls_back_when_count_is_out_of_range():
    too_few = _GOOD_PARAPHRASES[:3]
    result = variations("my S22's screen won't display anything", client=_scripted(too_few))
    assert MIN_COUNT <= len(result) <= MAX_COUNT
    assert result != too_few


def test_llm_path_falls_back_on_low_diversity():
    """Ten near-identical sentences with one word swapped -- exactly the
    failure mode the plan calls out. Must not be accepted as-is."""
    barely_different = [f"my screen is black, attempt {i}" for i in range(9)]
    result = variations("my screen is black", client=_scripted(barely_different))
    assert result != barely_different


def test_llm_path_falls_back_on_malformed_json():
    result = variations("my screen is black", client=FakeClient("not json at all"))
    assert MIN_COUNT <= len(result) <= MAX_COUNT


def test_llm_path_falls_back_when_client_is_unavailable():
    result = variations("my screen is black", client=_NO_LLM)
    assert MIN_COUNT <= len(result) <= MAX_COUNT


# ----------------------------------------------------------- lexical_diversity


def test_lexical_diversity_is_zero_for_identical_texts():
    assert lexical_diversity(["same words here", "same words here"]) == 0.0


def test_lexical_diversity_is_high_for_disjoint_texts():
    assert lexical_diversity(["apple banana cherry", "dog elephant fox"]) == 1.0


def test_lexical_diversity_of_a_single_text_is_zero():
    assert lexical_diversity(["only one here"]) == 0.0
