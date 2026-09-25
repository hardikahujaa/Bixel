"""Sanity checks on app/matcher_labels.json itself. The precision/recall
regression test in test_matcher.py is only as trustworthy as this data --
these guard against silent corruption (a typo'd catalog id, a duplicated
example, a missing field) that would make the regression test pass or fail
for the wrong reason.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LABELS_PATH = REPO_ROOT / "app" / "matcher_labels.json"
DEEPLINKS_PATH = REPO_ROOT / "student_kit" / "deeplinks.json"


def _load_labels() -> list[dict]:
    with open(LABELS_PATH, encoding="utf-8") as f:
        return json.load(f)["labels"]


def _real_catalog_ids() -> set[str]:
    with open(DEEPLINKS_PATH, encoding="utf-8") as f:
        return {e["id"] for e in json.load(f)["deeplinks"]}


def test_labels_file_is_valid_json_with_expected_top_level_shape():
    with open(LABELS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert "_readme" in data
    assert isinstance(data["labels"], list)
    assert len(data["labels"]) > 0


def test_every_label_has_the_required_fields_with_correct_types():
    for label in _load_labels():
        assert isinstance(label.get("row_id"), str) and label["row_id"]
        assert isinstance(label.get("step_text"), str) and label["step_text"].strip()
        assert isinstance(label.get("acceptable_catalog_ids"), list)
        assert all(isinstance(cid, str) for cid in label["acceptable_catalog_ids"])
        assert isinstance(label.get("note"), str) and label["note"]


def test_no_duplicate_step_text_entries():
    texts = [label["step_text"] for label in _load_labels()]
    assert len(texts) == len(set(texts)), "a step_text appears more than once in the labeled set"


def test_has_a_meaningful_number_of_positive_and_negative_examples():
    labels = _load_labels()
    positives = [l for l in labels if l["acceptable_catalog_ids"]]
    negatives = [l for l in labels if not l["acceptable_catalog_ids"]]
    assert len(positives) >= 5, "too few positive examples to measure recall meaningfully"
    assert len(negatives) >= 5, "too few negative examples to measure precision meaningfully"


def test_every_acceptable_catalog_id_exists_in_the_real_catalog():
    """A typo'd DL id here would silently make a positive example
    unwinnable -- every future matcher change would look like a recall
    regression that isn't real."""
    real_ids = _real_catalog_ids()
    for label in _load_labels():
        for cid in label["acceptable_catalog_ids"]:
            assert cid in real_ids, f"{cid!r} (from label {label['step_text']!r}) is not a real catalog id"


def test_step_texts_are_substrings_of_the_actual_siis_content():
    """Every label claims to be pulled from real SIIS text. Verify it,
    normalizing only whitespace -- this is what keeps the labeled set
    honest as 'real sentences', not paraphrased or invented ones."""
    with open(REPO_ROOT / "student_kit" / "siis_responses.json", encoding="utf-8") as f:
        siis = json.load(f)
    all_content = " ".join(
        " ".join(r["siis_response"]["content"].split()) for r in siis["responses"]
    )

    for label in _load_labels():
        normalized_step = " ".join(label["step_text"].split())
        assert normalized_step in all_content, (
            f"step_text for {label['row_id']} not found verbatim (whitespace-normalized) "
            f"in student_kit/siis_responses.json: {label['step_text']!r}"
        )
