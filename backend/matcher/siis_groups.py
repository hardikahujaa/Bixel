"""Split SIIS documents into ``##`` section groups.

This exists to build and run the matcher's evaluation set against real data. It is
**not** the production extraction path -- turning SIIS text into Goals, Actions and
StepGroups is M1's ``extract()`` and is a separate piece of work.

Why groups and not sentences: the 11 unique SIIS documents yield 74 imperative step
sentences, and the most frequent are "Tap Apps.", "Tap Storage." and "Navigate to
Settings." Those have no specific settings target, so matching them individually is
matching noise. The intent lives one level up, in the ``##`` sections, whose headings
read "5. Touch Sensitivity Setting", "6. Perform a Factory Data Reset", "Step 4:
Clear the Email App's Cache and Data".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import REPO_ROOT

SIIS_PATH = REPO_ROOT / "student_kit" / "siis_responses.json"


@dataclass(frozen=True)
class SiisGroup:
    """One ``##`` section of one SIIS document."""

    doc_title: str
    heading: str
    lines: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        """Stable identifier used by the label set: ``<doc prefix>::<heading>``."""
        return f"{self.doc_title[:34]}::{self.heading}"

    @property
    def text(self) -> str:
        """Heading plus body, joined -- this is what gets passed as ``step_text``."""
        return f"{self.heading}. " + " ".join(self.lines)


def load_siis_documents(path: Path | str | None = None) -> dict[str, str]:
    """Return ``{document title: content}`` for the unique documents only.

    Only ~11 documents cover the 20 queries -- "Blank or black display on a Samsung
    phone or tablet" is reused six times, byte-identical -- so deduplicating by
    content keeps the evaluation set from counting the same section repeatedly.
    """
    target = Path(path) if path is not None else SIIS_PATH
    with open(target, encoding="utf-8") as handle:
        payload = json.load(handle)

    documents: dict[str, str] = {}
    seen_content: set[str] = set()
    for row in payload["responses"]:
        response = row["siis_response"]
        content = response["content"]
        if content in seen_content:
            continue
        seen_content.add(content)
        documents[response["title"]] = content
    return documents


def extract_groups(path: Path | str | None = None) -> list[SiisGroup]:
    """Split every unique document into its ``##`` sections.

    Sections with a heading but no body lines are dropped -- one document has an empty
    trailing heading, and a group with no content carries no signal either way.
    """
    groups: list[SiisGroup] = []
    for title, content in load_siis_documents(path).items():
        heading: str | None = None
        body: list[str] = []
        for raw_line in content.split("\n"):
            line = raw_line.strip()
            if line.startswith("##"):
                if heading and body:
                    groups.append(SiisGroup(title, heading, tuple(body)))
                heading = line.lstrip("#").strip()
                body = []
            elif heading and line and not line.startswith("#"):
                body.append(line)
        if heading and body:
            groups.append(SiisGroup(title, heading, tuple(body)))
    return [group for group in groups if group.heading]


def group_index(path: Path | str | None = None) -> dict[str, SiisGroup]:
    """Groups keyed by :attr:`SiisGroup.key`, for label lookup."""
    return {group.key: group for group in extract_groups(path)}
