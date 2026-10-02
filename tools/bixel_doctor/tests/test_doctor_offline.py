"""Offline tests: no phone needed. The live end-to-end test is in test_doctor_live.py (opt-in)."""
import json
from pathlib import Path

import pytest

from backend.matcher.matcher import MatchCandidate, MatchContext, get_matcher, match_deeplinks
from backend.matcher.siis_groups import extract_groups
from tools.bixel_doctor import doctor, preflight
from tools.bixel_doctor.controls import CONTROLS

DOCS = doctor.DOCS
CATALOG = {e["id"]: e for e in json.loads((Path(__file__).parents[3] / "student_kit" / "deeplinks.json").read_text())["deeplinks"]}


def _cand(cid: str, score: float, polarity: int) -> MatchCandidate:
    return MatchCandidate(catalog_id=cid, score=score, entry=CATALOG[cid], polarity=polarity, why="test")


def _step(heading_part: str) -> str:
    return next(g.text for g in extract_groups(DOCS) if heading_part in g.heading)


# ---- adaptive brightness: select by validation.key + polarity, never by top score ------------
def test_extra_brightness_outscoring_adaptive_brightness_is_ignored():
    # The real matcher output for this step is Extra brightness at 0.811 right behind 0.831; make it WIN.
    text = _step("Adaptive Brightness")
    cands = [_cand("DL-0104", 0.90, -1), _cand("DL-0226", 0.88, -1), _cand("DL-0020", 0.83, -1)]
    chosen, ctl, _ = doctor.choose_entry(cands, text)
    assert chosen.catalog_id == "DL-0020" and ctl.key == "Adaptive brightness"


def test_polarity_picks_enable_vs_disable():
    on = "Turn On Adaptive Brightness. Tap the Adaptive brightness switch to turn it on."
    off = "Turn Off Adaptive Brightness. Tap the Adaptive brightness switch to turn it off."
    both = [_cand("DL-0020", 0.83, -1), _cand("DL-0021", 0.83, 1)]
    assert doctor.choose_entry(both, on)[0].catalog_id == "DL-0021"
    assert doctor.choose_entry(both, off)[0].catalog_id == "DL-0020"


def test_no_direction_means_refuse_not_guess():
    text = "Adjust Adaptive Brightness. Open Settings, tap Display and look at the Adaptive brightness row."
    chosen, ctl, why = doctor.choose_entry([_cand("DL-0020", 0.83, -1), _cand("DL-0021", 0.83, 1)], text)
    assert chosen is None and "on or off" in why


def test_step_must_name_the_setting():
    chosen, _, _ = doctor.choose_entry([_cand("DL-0020", 0.9, -1)], "Turn off the thing. Tap the switch to turn it off.")
    assert chosen is None


# ---- screen timeout value -----------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("Tap Screen timeout. Select 5 minutes.", "300000"),
    ("Screen timeout: choose 30 seconds.", "30000"),
])
def test_timeout_value_comes_from_the_step(text, expected):
    assert doctor.resolve_value(CONTROLS["Screen timeout"], text)[0] == expected


@pytest.mark.parametrize("text", ["Tap Screen timeout and pick a longer time.", "Select 7 minutes for the Screen timeout."])
def test_timeout_refuses_missing_or_off_menu_value(text):
    value, why = doctor.resolve_value(CONTROLS["Screen timeout"], text)
    assert value is None and why


# ---- dark mode is applied only through cmd uimode, never the settings key -----------------------
def test_dark_mode_control_never_writes_the_settings_key(monkeypatch):
    sent = []
    monkeypatch.setattr("tools.adb_spike.adb.shell", lambda cmd, timeout=60: sent.append(cmd) or "")
    monkeypatch.setattr("tools.adb_spike.adb.settings_put", lambda *a: sent.append(("PUT",) + a))
    CONTROLS["Dark mode settings"].apply("no")
    CONTROLS["Dark mode settings"].restore("2", "yes")
    assert sent == ["cmd uimode night no", "cmd uimode night yes"]


def test_only_the_three_flagship_controls_exist():
    assert set(CONTROLS) == {"Adaptive brightness", "Screen timeout", "Dark mode settings"}


# ---- regression: dark mode only clears the matcher gate by ~0.009 ----------------------------------
DARK_BASELINE, DARK_TOLERANCE = 0.779, 0.005   # measured on the authored step; matcher gate is 0.77


def test_dark_mode_matcher_score_has_not_dropped():
    get_matcher()
    g = next(g for g in extract_groups(DOCS) if "Dark Mode" in g.heading)
    hits = [c for c in match_deeplinks(g.text, MatchContext(heading=g.heading)) if c.catalog_id == "DL-0078"]
    assert hits, ("DL-0078 (Dark mode settings) no longer clears the matcher gate. The Doctor's dark-mode "
                  "action is now dead. The embedding model, index, catalog or gate changed; re-run "
                  "backend.matcher.evaluate and re-derive.")
    assert hits[0].score >= DARK_BASELINE - DARK_TOLERANCE, (
        f"DL-0078 score fell to {hits[0].score:.3f} from {DARK_BASELINE}; the gate is 0.77, so one more "
        "small change and dark mode silently disappears.")
    assert (hits[0].entry["validation"] or {})["key"] == "Dark mode settings"


# ---- the right step is picked, and distractors are not ---------------------------------------------
@pytest.mark.parametrize("heading,cid,key", [
    ("Adaptive Brightness", "DL-0020", "Adaptive brightness"),
    ("Screen Timeout", "DL-0220", "Screen timeout"),
    ("Dark Mode", "DL-0078", "Dark mode settings"),
])
def test_flagship_step_selects_the_expected_entry(heading, cid, key):
    get_matcher()
    g = next(g for g in extract_groups(DOCS) if heading in g.heading)
    chosen, ctl, _ = doctor.choose_entry(match_deeplinks(g.text, MatchContext(heading=g.heading)), g.text)
    assert (chosen.catalog_id, ctl.key) == (cid, key)


def test_distractor_sections_trigger_nothing():
    get_matcher()
    for g in extract_groups(DOCS):
        if any(w in g.heading for w in ("Restart", "Sensor Area", "Battery Saving", "Update Device", "Clean", "Damage")):
            assert doctor.choose_entry(match_deeplinks(g.text, MatchContext(heading=g.heading)), g.text)[0] is None, g.heading


# ---- document selection: thin margin, so pin the calibration set -----------------------------------
@pytest.mark.parametrize("complaint,title_part", [
    ("The brightness on my Galaxy keeps changing by itself and I can't read it outside", "brightness"),
    ("my screen goes black after like 15 seconds while reading", "turns off too quickly"),
    ("all the apps are black and I can't see anything in the sun", "too dark"),
])
def test_right_document_chosen(complaint, title_part):
    title, _ = doctor.pick_document(complaint)
    assert title and title_part in title


@pytest.mark.parametrize("complaint", [
    "my cat is sick", "my phone speaker makes no sound during calls", "my battery drains by lunchtime",
    "wifi keeps disconnecting", "the camera photos are blurry",
])
def test_unrelated_complaints_are_refused(complaint):
    assert doctor.pick_document(complaint)[0] is None


# ---- preflight fails closed -------------------------------------------------------------------------
def test_preflight_reports_missing_adb(monkeypatch):
    monkeypatch.setattr("tools.adb_spike.adb.ADB", "/nonexistent/adb")
    status, msg = preflight.check_device()
    assert status == preflight.BAD and "cannot run adb" in msg
