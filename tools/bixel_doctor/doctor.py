"""Bixel Doctor: complaint in -> grounded step -> catalog entry -> real adb action -> OS-verified result.

    python -m tools.bixel_doctor.doctor "My screen turns off too fast while I read" [--keep]

Pipeline (every stage can say "no" honestly and then nothing is touched):
  1. pick the SIIS-style document closest to the complaint (embedding similarity, with a floor)
  2. split it into ``##`` sections and run each through OUR matcher (backend.matcher.matcher)
  3. keep only candidates whose catalog ``validation.key`` has a Control, and whose own
     name appears in the step text; choose by key + polarity, NEVER by top score alone
  4. resolve the value (direction from polarity; timeout from the step's own number)
  5. read before-state, apply, read back, and confirm the OS state changed, not just the stored value
  6. put the original back and read it back again -- ALWAYS, unless --keep is passed explicitly
     (default is no lasting change to the phone; a failed verification is always restored)

Separate from app/, backend/ and the graded API: it only imports the matcher, read-only.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

from backend.matcher.embedder import cosine_scores, embed_documents, embed_queries
from backend.matcher.matcher import MatchContext, get_matcher, match_deeplinks
from backend.matcher.polarity import resolve_polarity
from backend.matcher.siis_groups import extract_groups, load_siis_documents
from tools.adb_spike import adb

from .controls import CONTROLS, TIMEOUT_CHOICES_MS, Control, read_only_diagnostics

DOCS = Path(__file__).parent / "authored_siis.json"
#: Complaint-to-document similarity floor. Calibrated in tests/test_doctor_offline.py
#: against unrelated complaints; below it we refuse rather than guess a document.
DOC_FLOOR = 0.66


@dataclass
class DoctorResult:
    complaint: str
    verdict: str                      # APPLIED_VERIFIED | ALREADY_SET | NO_ACTION | FAILED
    reason: str = ""
    document: str | None = None
    step: str | None = None
    catalog_id: str | None = None
    validation_key: str | None = None
    matcher_score: float | None = None
    polarity: int | None = None          # the catalog entry's own direction (0 = neutral entry)
    step_polarity: int | None = None     # what the step text asked for (-1 off, +1 on)
    value_applied: str | None = None
    command: str | None = None
    before: dict | None = None
    after: dict | None = None
    effect_verified: bool = False
    reverted: dict | None = None
    other_actionable_steps: list = field(default_factory=list)
    read_only_diagnostics: dict | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


# ---- stage 1 --------------------------------------------------------------------------
def pick_document(complaint: str, docs_path: Path = DOCS) -> tuple[str | None, float]:
    docs = load_siis_documents(docs_path)
    titles = list(docs)
    # Compare the complaint with each document's own title + heading text, not a stored example complaint.
    texts = [t + ". " + " ".join(g.heading for g in extract_groups(docs_path) if g.doc_title == t) for t in titles]
    scores = cosine_scores(embed_queries([complaint])[0], embed_documents(texts))
    i = int(scores.argmax())
    return (titles[i], float(scores[i])) if scores[i] >= DOC_FLOOR else (None, float(scores[i]))


# ---- stage 3/4 ------------------------------------------------------------------------
def choose_entry(candidates, step_text: str):
    """Return (candidate, control, reason). Selection is by validation.key and polarity.

    "Extra brightness" and "Adaptive color tone" routinely score within 0.02 of "Adaptive
    brightness"; they have no Control so they are dropped here, and a step that does not name
    the setting itself is dropped too. The top matcher score is only a tie-break.
    """
    step_pol = resolve_polarity(step_text)
    usable = []
    for c in candidates:
        key = (c.entry.get("validation") or {}).get("key")
        ctl = CONTROLS.get(key)
        if ctl and ctl.terms.search(step_text):
            usable.append((c, ctl))
    if not usable:
        return None, None, "no matched catalog entry has a control that the step text names"
    ctl = max(usable, key=lambda cc: cc[0].score)[1]
    same = [c for c, k in usable if k.key == ctl.key]
    if ctl.directional:
        if step_pol == 0:
            return None, ctl, f"{ctl.label}: the step does not say whether to turn it on or off"
        same = [c for c in same if c.polarity in (step_pol, 0)]
        exact = [c for c in same if c.polarity == step_pol]
        same = exact or same
        if not same:
            return None, ctl, f"{ctl.label}: no catalog entry with direction {step_pol:+d}"
    return max(same, key=lambda c: c.score), ctl, ""


def resolve_value(ctl: Control, step_text: str) -> tuple[str | None, str]:
    pol = resolve_polarity(step_text)
    if ctl.key == "Adaptive brightness":
        return ("1" if pol > 0 else "0"), ""
    if ctl.key == "Dark mode settings":
        return ("yes" if pol > 0 else "no"), ""
    m = re.search(r"(\d+)\s*(second|minute)s?", step_text, re.I)   # Screen timeout
    if not m:
        return None, "Screen timeout: the step names no duration"
    ms = int(m.group(1)) * (1000 if m.group(2).lower() == "second" else 60_000)
    if ms not in TIMEOUT_CHOICES_MS:
        return None, f"Screen timeout: {ms} ms is not one of Samsung's menu choices"
    return str(ms), ""


# ---- the whole thing ------------------------------------------------------------------
def diagnose_and_fix(complaint: str, *, keep: bool = False, docs_path: Path = DOCS) -> DoctorResult:
    res = DoctorResult(complaint=complaint, verdict="NO_ACTION")
    get_matcher()
    doc, sim = pick_document(complaint, docs_path)
    if doc is None:
        res.reason = f"no document is close enough to this complaint (best similarity {sim:.2f} < {DOC_FLOOR})"
        return res
    res.document = doc

    plans = []
    for g in (g for g in extract_groups(docs_path) if g.doc_title == doc):
        cands = match_deeplinks(g.text, MatchContext(heading=g.heading))
        cand, ctl, why = choose_entry(cands, g.text)
        if cand and ctl:
            value, vwhy = resolve_value(ctl, g.text)
            if value is not None:
                plans.append((cand, ctl, value, g))
            else:
                res.reason = vwhy
        elif why and not res.reason:
            res.reason = why
    if not plans:
        res.reason = res.reason or "the document has no step this catalog can act on"
        return res
    plans.sort(key=lambda p: -p[0].score)
    cand, ctl, value, g = plans[0]
    res.reason = ""   # reasons from distractor steps are irrelevant once an action is chosen
    res.other_actionable_steps = [p[3].heading for p in plans[1:]]
    res.step, res.catalog_id = g.heading, cand.catalog_id
    res.validation_key, res.matcher_score = ctl.key, round(cand.score, 3)
    res.polarity, res.value_applied = cand.polarity, value
    res.step_polarity = resolve_polarity(g.text)
    res.read_only_diagnostics = read_only_diagnostics()

    if not adb.device_connected():
        res.verdict, res.reason = "FAILED", "no authorised device"
        return res
    want_os = ctl.expected_os(value)
    res.before = ctl.snapshot()
    if res.before["os"] == want_os:
        res.verdict, res.reason, res.after = "ALREADY_SET", f"{ctl.label} is already in the requested state; nothing changed", res.before
        return res
    try:
        res.command = ctl.apply(value)
        seen = ctl.settle(want_os)
        res.after = ctl.snapshot()
        res.effect_verified = seen == want_os
        res.verdict = "APPLIED_VERIFIED" if res.effect_verified else "FAILED"
        if not res.effect_verified:
            res.reason = f"stored value changed but the OS reports {seen!r}, expected {want_os!r}"
    except Exception as e:  # noqa: BLE001 - any failure must lead to a restore, then be reported
        res.verdict, res.reason = "FAILED", f"{type(e).__name__}: {e}"
    if not keep or res.verdict == "FAILED":
        try:
            ctl.restore(res.before["stored"], res.before["os"])
            ctl.settle(res.before["os"]) if res.before["os"] else None
            back = ctl.snapshot()
            res.reverted = {"state": back, "restore_confirmed": back["os"] == res.before["os"]
                            and (back["stored"] == res.before["stored"] or ctl.key == "Dark mode settings")}
        except Exception as e:  # noqa: BLE001
            res.reverted = {"restore_confirmed": False, "error": f"{type(e).__name__}: {e}"}
    return res


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    r = diagnose_and_fix(" ".join(args), keep="--keep" in sys.argv)
    print(r.to_json())
    sys.exit(0 if r.verdict in ("APPLIED_VERIFIED", "ALREADY_SET", "NO_ACTION") else 2)
