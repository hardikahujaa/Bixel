"""Do the keys we found map to real entries in student_kit/deeplinks.json?

Runs each setting through OUR OWN matcher (backend.matcher.matcher.match_deeplinks), the same
code the graded route uses, and also does an independent catalog scan so a matcher abstention can
be told apart from "the catalog really has no such entry".

    python -m tools.adb_spike.map_to_catalog          # from the repo root

The touch-sensitivity step text is a real SIIS section (row_21). The other four have no matching
section in the 20 sample SIIS documents, so their step text is SIIS-style wording written here
and labelled `synthetic` in the output.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.matcher.matcher import MatchContext, get_matcher, match_deeplinks

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "student_kit" / "deeplinks.json").read_text())["deeplinks"]
SIIS = json.loads((ROOT / "student_kit" / "siis_responses.json").read_text())["responses"]


def _real_touch_section() -> tuple[str, str]:
    for row in SIIS:
        for sec in re.split(r"\n(?=##? )", row["siis_response"]["content"]):
            if sec.lstrip().startswith("## 5. Touch Sensitivity Setting"):
                head, _, body = sec.strip().partition("\n")
                return head.lstrip("# ").strip(), body.strip()
    raise RuntimeError("touch-sensitivity SIIS section not found")


_t_head, _t_body = _real_touch_section()
CASES = {
    "touch sensitivity": ("real SIIS row_21", _t_head, _t_body, r"touch sensitiv"),
    "adaptive brightness": ("synthetic", "Adjust Adaptive Brightness",
        "If the screen is too dark or too bright, go to Settings, tap Display, and turn Adaptive brightness off or on.",
        r"adaptive brightness"),
    "motion smoothness / refresh rate": ("synthetic", "Change Motion Smoothness",
        "To make scrolling smoother or save battery, go to Settings, tap Display, tap Motion smoothness and choose Adaptive or Standard.",
        r"motion smoothness|refresh rate"),
    "screen timeout": ("synthetic", "Extend the Screen Timeout",
        "If the screen turns off too quickly, go to Settings, tap Display, tap Screen timeout and select a longer time.",
        r"screen timeout|screen off|screen turns off"),
    "dark mode": ("synthetic", "Turn Off Dark Mode",
        "If colours look wrong, go to Settings, tap Display, and select Light instead of Dark.",
        r"dark mode|dark theme|\bdark\b"),
}


def main() -> None:
    get_matcher()
    out = {}
    for name, (source, heading, text, scan) in CASES.items():
        cands = match_deeplinks(step_text=text, ctx=MatchContext(heading=heading))
        scan_hits = [e["id"] + ": " + e["qna_description"][:80] for e in CATALOG
                     if re.search(scan, (e["description"] + " " + e["qna_description"] + " " + e["validation"]["key"]
                                          if e.get("validation") else e["description"]), re.I)]
        out[name] = {
            "step_text_source": source,
            "matcher_candidates": [{"id": c.catalog_id, "score": round(c.score, 3), "polarity": c.polarity,
                                    "validation_key": (c.entry.get("validation") or {}).get("key"),
                                    "means": c.entry["qna_description"][:90]} for c in cands],
            "catalog_scan_hits": len(scan_hits),
            "catalog_scan_examples": scan_hits[:4],
        }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
