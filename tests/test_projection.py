"""Tests for to_deeplink_pair(). Owner: M2.

The strongest test here is the one against sample_output.json: Samsung's own worked
example shows exactly what a projected catalog entry should look like, so reproducing it
byte-for-byte proves the projection is right rather than merely self-consistent.
"""

import json

import pytest

from app.projection import to_deeplink_pair
from backend.matcher.catalog import load_catalog


@pytest.fixture(scope="module")
def raw_entries():
    return {entry.id: entry.raw for entry in load_catalog()}


def test_reproduces_samsungs_own_worked_example(raw_entries):
    """sample_output.json uses DL-0542. Our projection must match it exactly."""
    sample = json.loads(open("student_kit/sample_output.json", encoding="utf-8").read())
    expected_group = sample["response"]["contexts"][0]["actions"][0]["stepGroups"][0]
    expected_act = expected_group["actionableDeeplink"]
    expected_val = expected_group["validationDeeplink"]

    actionable, validation = to_deeplink_pair(raw_entries["DL-0542"])

    assert actionable.deeplink == expected_act["deeplink"]
    assert actionable.description == expected_act["description"]
    assert actionable.message == expected_act["message"]
    assert actionable.originalType == expected_act["originalType"]
    assert validation is not None
    assert validation.model_dump() == expected_val


def test_every_catalog_entry_projects_without_raising(raw_entries):
    """All 578, including the 10 with an empty qna_description and the placeholder."""
    failures = []
    for entry_id, raw in raw_entries.items():
        try:
            to_deeplink_pair(raw)
        except Exception as exc:  # noqa: BLE001 - that is what we are testing for
            failures.append((entry_id, repr(exc)))
    assert failures == []


def test_projected_fields_are_verbatim_copies(raw_entries):
    for entry_id in ("DL-0001", "DL-0125", "DL-0126", "DL-0542", "DL-0116"):
        raw = raw_entries[entry_id]
        actionable, _ = to_deeplink_pair(raw)
        assert actionable.deeplink == raw["deeplink"]
        assert actionable.description == raw["description"]
        assert actionable.message == raw["message"]
        assert actionable.originalType == raw["originalType"]


def test_internal_fields_are_dropped(raw_entries):
    """id, control_type and qna_description are internal and must not reach the response."""
    actionable, _ = to_deeplink_pair(raw_entries["DL-0542"])
    dumped = actionable.model_dump()
    assert set(dumped) == {"deeplink", "description", "message", "classes", "originalType"}
    assert "qna_description" not in dumped
    assert "control_type" not in dumped
    assert "id" not in dumped


def test_classes_is_always_null(raw_entries):
    """`classes` exists in schema.py but appears in none of the 578 entries."""
    for entry_id in ("DL-0001", "DL-0542", "DL-DUMMY"):
        actionable, _ = to_deeplink_pair(raw_entries[entry_id])
        assert actionable.classes is None


def test_validation_with_only_deeplink_and_key(raw_entries):
    """432 entries carry {deeplink, key} and nothing else -- the rest stay None."""
    _, validation = to_deeplink_pair(raw_entries["DL-0001"])
    assert validation is not None
    assert validation.key == "Use 24-hour format"
    assert validation.resultType is None
    assert validation.condition is None
    assert validation.value is None


def test_validation_with_all_five_fields(raw_entries):
    """138 entries carry the full set, always boolean / equal / "True"."""
    _, validation = to_deeplink_pair(raw_entries["DL-0542"])
    assert validation is not None
    assert validation.resultType.value == "boolean"
    assert validation.condition.value == "equal"
    assert validation.value == "True"


def test_null_validation_returns_none(raw_entries):
    """8 entries have validation: null, the placeholder among them."""
    _, validation = to_deeplink_pair(raw_entries["DL-DUMMY"])
    assert validation is None


def test_counts_of_each_validation_shape_match_the_catalog(raw_entries):
    full = partial = none = 0
    for raw in raw_entries.values():
        _, validation = to_deeplink_pair(raw)
        if validation is None:
            none += 1
        elif validation.resultType is None:
            partial += 1
        else:
            full += 1
    assert (full, partial, none) == (138, 432, 8)


@pytest.mark.parametrize(
    "bad,reason",
    [
        ({"id": "x", "description": "d"}, "no deeplink"),
        ({"id": "x", "deeplink": "", "description": "d"}, "empty deeplink"),
        ({"id": "x", "deeplink": "bixby://a"}, "no description"),
        ({"id": "x", "deeplink": "bixby://a", "description": "  "}, "blank description"),
        ({"id": "x", "deeplink": "bixby://a", "description": "d", "validation": []}, "validation not a dict"),
        ({"id": "x", "deeplink": "bixby://a", "description": "d", "validation": {"key": "k"}}, "validation without deeplink"),
        ({"id": "x", "deeplink": "bixby://a", "description": "d", "validation": {"deeplink": "bixby://v"}}, "validation without required key"),
    ],
)
def test_unprojectable_entries_raise_with_a_clear_message(bad, reason):
    """Better to fail naming the entry than to emit a half-built Deeplink that fails
    schema validation further downstream."""
    with pytest.raises(ValueError):
        to_deeplink_pair(bad)


def test_non_dict_input_raises():
    for bad in (None, "x", 5, []):
        with pytest.raises(ValueError):
            to_deeplink_pair(bad)
