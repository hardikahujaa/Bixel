"""Catalog -> schema projection. Owner: M2 (docs/PLAN.md, "Who owns what").

to_deeplink_pair(catalog_entry) -> (Deeplink, ValidationDeepLink | None)

Pure projection, no matching logic. Copies deeplink/description/message/
originalType verbatim into Deeplink (classes stays null — it never appears
in the catalog), and the entry's whole "validation" object verbatim into
ValidationDeepLink (or None when the entry's validation is null).
"""
from typing import Optional

from student_kit.schema import Deeplink, ValidationDeepLink


def to_deeplink_pair(catalog_entry: dict) -> tuple[Deeplink, Optional[ValidationDeepLink]]:
    raise NotImplementedError("M2: pure verbatim projection, see docstring")
