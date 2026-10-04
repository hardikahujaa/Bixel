"""Offline tests for the round-2 settings harness. No phone needed.

Each test pins a defect found auditing `settings_matrix.py` before merging it to main.
The harness itself only ever runs against a real device, so the verdict logic is the part
that can be checked here -- and it is the part whose output lands in FINDINGS_2.md.
"""
import pytest

from tools.adb_spike import settings_matrix as sm


def test_the_standalone_scripts_use_their_own_copy_of_the_adb_module():
    """Documenting a quirk, not asserting it is good. probe/toggle_test/discover_setting/
    settings_matrix put their own directory on sys.path and `import adb`, so the module is
    loaded a second time as top-level `adb`; controls.py and preflight.py import
    `tools.adb_spike.adb`. Both copies resolve the same binary, so nothing misbehaves -- but
    patching one does not affect the other, which is why the fixture below patches sm.adb.
    If the scripts are ever switched to the package import, delete this test."""
    from tools.adb_spike import adb as package_adb
    assert sm.adb is not package_adb
    assert sm.adb.__name__ == "adb" and package_adb.__name__ == "tools.adb_spike.adb"


@pytest.fixture
def fake_phone(monkeypatch):
    """A phone whose three namespaces hold exactly what we put there."""
    state = {"global": {"zen_mode": "0"}, "system": {}, "secure": {}}

    monkeypatch.setattr(sm.adb, "settings_list", lambda ns: dict(state[ns]))
    monkeypatch.setattr(sm.adb, "settings_get", lambda ns, key: state[ns].get(key))
    monkeypatch.setattr(sm.adb, "settings_put", lambda ns, key, v: state[ns].__setitem__(key, str(v)))
    monkeypatch.setattr(sm.time, "sleep", lambda _s: None)
    return state


def _evidence_spec(**kw):
    """A dnd_key-shaped row: the write sticks, but the OS state never moves."""
    return sm.Spec(
        id="dnd_key", label="DND via settings key", catalog="DL-0505/0506",
        writes=[("global", "zen_mode", "1")],
        probe=lambda: "OFF",
        expect=lambda b, a: b == "OFF" and a not in (None, "OFF"),
        expected_negative=True, **kw)


def test_an_expected_negative_with_a_clean_cycle_is_xfail(fake_phone):
    """The old if/elif gave this row the PASS verdict first, which matched neither branch,
    so it reported NO-EFFECT while FINDINGS_2.md recorded XFAIL."""
    r = sm.run(_evidence_spec())
    assert r["effect"] == "NOT-OBSERVED"
    assert r["verdict"] == "XFAIL", r
    assert "kept as evidence" in r["note"]


def test_xfail_does_not_fail_the_run():
    """XFAIL is an expected outcome. It was missing from main()'s accepted list, which only
    went unnoticed because the ordering bug above made XFAIL unreachable."""
    source = (sm.OUT.parent / "settings_matrix.py").read_text(encoding="utf-8")
    accepted = source.split("accepted = (")[1].split(")")[0]
    assert '"XFAIL"' in accepted, f"accepted verdicts: {accepted}"


def test_a_setting_with_no_effect_probe_is_stored_only(fake_phone):
    spec = sm.Spec(id="touchhold", label="Touch and hold delay", catalog="DL-0234",
                   writes=[("secure", "long_press_timeout", "1000")])
    fake_phone["secure"]["long_press_timeout"] = "500"
    r = sm.run(spec)
    assert r["effect"] == "stored-only"
    assert r["verdict"] == "PASS"
    assert fake_phone["secure"]["long_press_timeout"] == "500", "original must be restored"


def test_an_observed_effect_passes(fake_phone):
    seen = iter(["true", "false"])
    spec = sm.Spec(id="extradim", label="Extra dim", catalog="DL-0203/0204",
                   writes=[("secure", "reduce_bright_colors_activated", "0")],
                   probe=lambda: next(seen),
                   expect=lambda b, a: b != a and a is not None)
    fake_phone["secure"]["reduce_bright_colors_activated"] = "1"
    r = sm.run(spec)
    assert r["effect"] == "observed" and r["verdict"] == "PASS"


# ---- the note was overwritten, and claimed "unset" on a key that was present -----------------
def test_note_does_not_claim_unset_for_a_key_that_was_present(fake_phone):
    """`if spec.treat_unset_as:` fired whenever the spec merely declared a default, so a
    present key was reported as having been unset."""
    fake_phone["global"]["adaptive_power_saving_setting"] = "0"
    spec = sm.Spec(id="adaptivepower", label="Adaptive power saving", catalog="DL-0399/0400",
                   writes=[("global", "adaptive_power_saving_setting", "1")],
                   treat_unset_as={"global.adaptive_power_saving_setting": "0"})
    r = sm.run(spec)
    assert "was unset" not in r.get("note", ""), r


def test_note_does_say_unset_when_the_key_really_was_unset(fake_phone):
    spec = sm.Spec(id="inversion", label="Color inversion", catalog="DL-0280/0281",
                   writes=[("secure", "accessibility_display_inversion_enabled", "1")],
                   treat_unset_as={"secure.accessibility_display_inversion_enabled": "0"})
    r = sm.run(spec)
    assert "was unset before" in r["note"], r


def test_the_bluetooth_warning_survives_a_second_note(fake_phone, monkeypatch):
    """Two unconditional assignments meant the later note replaced the earlier one. The
    connected-device warning is the one that must never be dropped."""
    fake_phone["global"]["mode_ringer"] = "2"
    spec = _evidence_spec(connection_keys=r"^global\.mode_")

    calls = {"n": 0}

    def put(ns, key, v):
        calls["n"] += 1
        fake_phone[ns][key] = str(v)
        if calls["n"] == 1:                       # the write also disturbs a connection key
            fake_phone["global"]["mode_ringer"] = "0"

    monkeypatch.setattr(sm.adb, "settings_put", put)
    r = sm.run(spec)
    assert "reconnect the device" in r["note"], r
    assert r["connection_state_keys_changed"] == ["global.mode_ringer"], r
