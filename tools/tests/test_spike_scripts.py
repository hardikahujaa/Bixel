"""Offline regression tests for the spike scripts. No phone needed.

Each test here pins a bug found auditing the adb/laya spike branches before merging them
to main. The Doctor's own tests live in tools/bixel_doctor/tests/.
"""
import importlib
import json
import pathlib

import pytest

from tools.adb_spike import adb, toggle_test
from tools.laya_spike import compare

TOOLS = pathlib.Path(__file__).parents[1]


# ---- probe.py was not importable as a module on Windows --------------------------------------
def test_probe_is_importable_as_a_module():
    """``sys.path.insert(0, __file__.rsplit("/", 1)[0])`` finds no "/" on Windows, so it
    inserted the file's own path and ``import adb`` failed for every entry point except a
    direct ``python probe.py``."""
    probe = importlib.import_module("tools.adb_spike.probe")
    assert probe.adb is not None


# ---- a missing adb binary must be an AdbError, not a bare OSError from subprocess ------------
def test_missing_adb_binary_raises_adberror(monkeypatch):
    monkeypatch.setattr(adb, "ADB", str(TOOLS / "no-such-adb-binary"))
    with pytest.raises(adb.AdbError, match="cannot run adb"):
        adb.device_connected()


def test_probe_reports_an_adb_error_as_json_not_a_traceback(monkeypatch, capsys):
    probe = importlib.import_module("tools.adb_spike.probe")
    monkeypatch.setattr(probe.adb, "ADB", str(TOOLS / "no-such-adb-binary"))
    assert probe.main() == 1
    assert "cannot run adb" in json.loads(capsys.readouterr().out)["error"]


def test_probe_temperature_is_none_when_unreadable(monkeypatch):
    probe = importlib.import_module("tools.adb_spike.probe")
    monkeypatch.setattr(probe.adb, "shell", lambda cmd, timeout=60: "  level: 55\n")
    assert probe.battery()["temperature_celsius"] is None


# ---- toggle_test.py scored an unreadable probe as a verified effect --------------------------
def _spec(effect_values, **kw):
    """A Spec whose effect probe yields `effect_values` in order."""
    seq = list(effect_values)
    return toggle_test.Spec(
        name="t", ns="system", key="k", test_value="0", catalog_query="q",
        effect_probe=(lambda: seq.pop(0)) if effect_values else None, **kw)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(toggle_test.time, "sleep", lambda _s: None)


def test_unreadable_effect_probe_is_not_a_verified_effect():
    """Both reads None stringified to "None -> None", which the old comparison read as a
    change. An unreadable probe is the absence of evidence, not evidence."""
    state = {"v": "1"}
    s = _spec([None, None], getter=lambda: state["v"], putter=lambda v: state.update(v=v))
    r = toggle_test.run_cycle(s)
    assert r["effect_verified"] is False
    assert r["verdict"] == "FAIL"
    assert state["v"] == "1", "the original value must still be restored"


def test_a_real_effect_change_still_passes():
    state = {"v": "1"}
    s = _spec(["true", "false"], getter=lambda: state["v"], putter=lambda v: state.update(v=v))
    r = toggle_test.run_cycle(s)
    assert r["verdict"] == "PASS" and r["effect_verified"] is True


def test_no_effect_observed_fails():
    state = {"v": "1"}
    s = _spec(["true", "true"], getter=lambda: state["v"], putter=lambda v: state.update(v=v))
    r = toggle_test.run_cycle(s)
    assert r["verdict"] == "FAIL" and r["effect_verified"] is False


def test_restore_failure_is_reported_and_names_the_key():
    """A failed restore left the phone changed and surfaced as a traceback with no
    instruction to put the setting back."""
    calls = {"n": 0}

    def putter(v):
        calls["n"] += 1
        if calls["n"] > 1:            # the restore write
            raise adb.AdbError("device offline")

    s = _spec(["true", "false"], getter=lambda: "0" if calls["n"] else "1", putter=putter)
    r = toggle_test.run_cycle(s)
    assert r["verdict"] == "FAIL"
    assert "RESTORE FAILED" in r["note"] and "system.k" in r["note"]


# ---- compare.py counted an abstention as an action on the hard-gate column -------------------
def _write(out: pathlib.Path, name: str, rows: list[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")


def test_abstention_counts_as_a_refusal_not_an_action(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(compare, "OUT", tmp_path)
    abstained = [{"complaint": "my cat is sick", "label": "none", "pred": None,
                  "abstained": True, "conf": 0.4, "ms": 300.0}]
    _write(tmp_path, "baseline", [{**abstained[0], "pred": "none", "score": 0.5}])
    _write(tmp_path, "laya", abstained)
    compare.main()
    out = capsys.readouterr().out
    assert "acted-on-off-topic=0/1" in out, out
    assert "wrong-class=0" in out, out
    assert "abstained=1/1" in out, out


def test_compare_explains_a_missing_input_instead_of_tracebacking(monkeypatch, tmp_path):
    monkeypatch.setattr(compare, "OUT", tmp_path)
    with pytest.raises(SystemExit) as e:
        compare.load("baseline")
    assert "laya_spike.baseline" in str(e.value)


def test_compare_rejects_an_empty_input(monkeypatch, tmp_path):
    monkeypatch.setattr(compare, "OUT", tmp_path)
    _write(tmp_path, "laya", [])
    with pytest.raises(SystemExit, match="empty"):
        compare.load("laya")


# ---- printed strings must survive a legacy Windows console ------------------------------------
def test_tool_sources_are_ascii_only():
    """These scripts print to a Windows console. cp1252 encodes an em dash; cp437 raises
    UnicodeEncodeError mid-print, which would kill a demo."""
    offenders = []
    for path in sorted(TOOLS.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(ord(c) > 127 for c in line):
                offenders.append(f"{path.relative_to(TOOLS.parent)}:{lineno}")
    assert not offenders, f"non-ASCII in printed sources: {offenders}"
