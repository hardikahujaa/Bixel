"""Catalog -> schema projection. Owner: M2 (docs/PLAN.md, "Who owns what").

to_deeplink_pair(catalog_entry) -> (Deeplink, ValidationDeepLink | None)

Pure projection, no matching logic. Copies deeplink/description/message/
originalType verbatim into Deeplink (classes stays null -- it never appears
in the catalog), and the entry's whole "validation" object verbatim into
ValidationDeepLink (or None when the entry's validation is null).

This is the ONLY place a catalog entry becomes a response Deeplink. The matcher in
backend/matcher deliberately hands back the raw entry untouched so this rule lives in
one place and cannot drift between two implementations.

Verbatim means verbatim. No normalising, no title-casing, no filling in blanks. The
catalog is the single source of truth for every URI we are allowed to emit, and A2
scores exact-match deeplink validity.
"""
from typing import Any, Optional

from student_kit.schema import Deeplink, ValidationDeepLink

#: Fields of a catalog entry that belong in the response Deeplink. Everything else on
#: the entry (id, control_type, qna_description, validation) is internal.
_DEEPLINK_FIELDS = ("deeplink", "description", "message", "originalType")

#: Fields of a catalog entry's "validation" object. `key` is required by the schema;
#: the rest are optional and, in this catalog, only ever appear together.
_VALIDATION_FIELDS = ("deeplink", "key", "resultType", "condition", "value")


def to_deeplink_pair(
    catalog_entry: dict[str, Any],
) -> tuple[Deeplink, Optional[ValidationDeepLink]]:
    """Project one catalog entry onto the response schema.

    Raises ValueError on an entry that cannot be projected, rather than emitting a
    half-built Deeplink. A missing `deeplink` or `description` would produce a response
    that fails schema validation downstream, and failing here names the offending entry.
    """
    if not isinstance(catalog_entry, dict):
        raise ValueError(f"expected a catalog entry dict, got {type(catalog_entry).__name__}")

    entry_id = catalog_entry.get("id", "<no id>")

    uri = catalog_entry.get("deeplink")
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError(f"catalog entry {entry_id} has no usable 'deeplink'")

    description = catalog_entry.get("description")
    if not isinstance(description, str) or not description.strip():
        # Deeplink.description is a required str in schema.py.
        raise ValueError(f"catalog entry {entry_id} has no usable 'description'")

    actionable = Deeplink(
        deeplink=uri,
        description=description,
        message=catalog_entry.get("message") or "",
        originalType=catalog_entry.get("originalType"),
        # classes is absent from all 578 catalog entries. Left null rather than invented.
        classes=None,
    )

    return actionable, _to_validation(catalog_entry, entry_id)


def _to_validation(
    catalog_entry: dict[str, Any], entry_id: Any
) -> Optional[ValidationDeepLink]:
    """Copy the entry's `validation` object, or return None when it has none.

    Three shapes exist across the catalog: 432 entries carry {deeplink, key}, 138 carry
    all five fields (always boolean / equal / "True"), and 8 carry null. All three are
    handled without inventing values for the absent fields.
    """
    validation = catalog_entry.get("validation")
    if validation is None:
        return None
    if not isinstance(validation, dict):
        raise ValueError(
            f"catalog entry {entry_id} has a non-dict 'validation': {type(validation).__name__}"
        )

    uri = validation.get("deeplink")
    key = validation.get("key")
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError(f"catalog entry {entry_id} validation has no 'deeplink'")
    if not isinstance(key, str) or not key.strip():
        # ValidationDeepLink.key is required by schema.py.
        raise ValueError(f"catalog entry {entry_id} validation has no 'key'")

    return ValidationDeepLink(
        deeplink=uri,
        key=key,
        resultType=validation.get("resultType"),
        condition=validation.get("condition"),
        value=validation.get("value"),
    )
