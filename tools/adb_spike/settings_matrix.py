"""Run the safe cycle on additional settings, with full-namespace side-effect and restore checks.

    python tools/adb_spike/settings_matrix.py [id ...] [--json]

Per setting: snapshot all three namespaces twice (self-moving keys become the noise filter) ->
read original -> write -> read back -> OS-effect probe -> restore -> read back -> snapshot again.
PASS needs: write read back AND restore confirmed AND the whole namespace identical to the start.
`effect` is one of: observed / NOT-OBSERVED / stored-only (no OS-visible state found over adb).
Keys that moved *besides* the one we wrote are reported as side effects, then restored.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import adb  # noqa: E402

OUT = Path(__file__).parent / "out"
ALWAYS_NOISE = {"system.settings_change_history"}
# OS-internal version counters bumped by the change itself. Allow-listed AND reported (benign_changed), never hidden.
BENIGN_COUNTERS = {"global.zen_mode_config_etag"}


def snap() -> dict[str, str]:
    return {f"{ns}.{k}": v for ns in adb.NAMESPACES for k, v in adb.settings_list(ns).items()}


def moved(a: dict, b: dict) -> set[str]:
    return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}


def _m(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


@dataclass
class Spec:
    id: str
    label: str
    catalog: str
    writes: list[tuple[str, str, str]]            # (ns, key, test_value); original read live, restored after
    probe: object = None                          # callable -> str | None, the OS-visible state
    expect: object = None                         # callable(before, after) -> bool, did the probe change as wanted
    custom_get: object = None                     # for cmd-based settings (replaces `writes`)
    custom_put: object = None
    test_value: str = ""
    treat_unset_as: dict = field(default_factory=dict)   # {"ns.key": "0"}: unset == this default
    expected_negative: bool = False                    # evidence row: the OS is expected to ignore/revert this write
    connection_keys: str = ""                          # regex of keys owned by a connected device, not by us (reported, never hidden)
    settle_s: float = 2.5                              # radios need longer than settings keys; see mobiledata


SPECS = {s.id: s for s in [
    Spec("animations", "Reduce animations", "DL-0285/0286",
         [("global", "window_animation_scale", "0.5"), ("global", "transition_animation_scale", "0.5"),
          ("global", "animator_duration_scale", "0.5")]),
    Spec("aod", "Always On Display", "DL-0482/0483", [("system", "aod_mode", "0")]),
    # Writing global.zen_mode is stored but NOT applied by Android (same trap as dark mode); the working path is cmd.
    Spec("dnd", "Do not disturb (cmd notification set_dnd)", "DL-0505/0506", [],
         custom_get=lambda: _m(r"mZenMode=ZEN_MODE_(\w+)", adb.shell("dumpsys notification", timeout=90)),
         custom_put=lambda v: adb.shell("cmd notification set_dnd " + ("off" if v == "OFF" else "priority")),
         test_value="IMPORTANT_INTERRUPTIONS",
         probe=lambda: _m(r"mZenMode=ZEN_MODE_(\w+)", adb.shell("dumpsys notification", timeout=90)),
         expect=lambda b, a: b == "OFF" and a not in (None, "OFF")),
    Spec("dnd_key", "Do not disturb via settings key (expected: stored-only)", "DL-0505/0506", [("global", "zen_mode", "1")],
         probe=lambda: _m(r"mZenMode=ZEN_MODE_(\w+)", adb.shell("dumpsys notification", timeout=90)),
         expect=lambda b, a: b == "OFF" and a not in (None, "OFF"), expected_negative=True),
    Spec("eyecomfort", "Eye comfort shield", "DL-0039/0040", [("system", "blue_light_filter", "1")]),
    Spec("extradim", "Extra dim", "DL-0203/0204", [("secure", "reduce_bright_colors_activated", "0")],
         probe=lambda: _m(r"Reduce bright colors:\s*\n\s*Activated: (\w+)", adb.shell("dumpsys color_display", timeout=60)),
         expect=lambda b, a: b != a and a is not None),
    Spec("inversion", "Color inversion", "DL-0280/0281",
         [("secure", "accessibility_display_inversion_enabled", "1")],
         treat_unset_as={"secure.accessibility_display_inversion_enabled": "0"}),
    Spec("touchhold", "Touch and hold delay", "DL-0234", [("secure", "long_press_timeout", "1000")]),
    Spec("sound", "Sound mode (ringer)", "DL-0260", [],
         custom_get=lambda: _m(r"mode \(internal\) = (\w+)", adb.shell("dumpsys audio", timeout=60)),
         custom_put=lambda v: adb.shell(f"cmd audio set-ringer-mode {v}"),
         test_value=lambda orig: "VIBRATE" if orig == "NORMAL" else "NORMAL",   # always differ from the current state
         probe=lambda: _m(r"mode \(internal\) = (\w+)", adb.shell("dumpsys audio", timeout=60)),
         expect=lambda b, a: b != a and a is not None),
    # adb's low_power write yields the plain Android Battery Saver (2 keys), NOT Samsung's 27-key Power saving bundle.
    Spec("powersave", "Battery Saver via global.low_power (not Samsung's full Power saving)", "DL-0411/0412",
         [("global", "low_power", "1")],
         probe=lambda: _m(r"Battery Saver is currently: (\w+)", adb.shell("dumpsys power", timeout=60)),
         expect=lambda b, a: b == "OFF" and a == "ON"),
    # Samsung's Adaptive power saving; key found by a manual flip. Probe candidate: IntelligentBatterySaverService "policy status".
    Spec("adaptivepower", "Adaptive power saving", "DL-0399/0400", [("global", "adaptive_power_saving_setting", "1")],
         probe=lambda: _m(r"policy status : (\d+)", adb.shell("dumpsys IntelligentBatterySaverService", timeout=60)),
         expect=lambda b, a: b != a and a is not None,
         treat_unset_as={"global.adaptive_power_saving_setting": "0"}),
    Spec("bluetooth", "Bluetooth", "DL-0494/0495", [],
         custom_get=lambda: _m(r"\n\s*state: (\w+)", adb.shell("dumpsys bluetooth_manager", timeout=60)),
         custom_put=lambda v: adb.shell("cmd bluetooth_manager " + ("enable" if v == "ON" else "disable")),
         test_value="OFF", connection_keys=r"^(system\.buds_|system\.SOUNDALIVE|secure\.bt_a2dp|system\.volume_music_bt|global\.mode_|system\.volume_system_|system\.next_alarm_formatted)",
         probe=lambda: _m(r"\n\s*enabled: (\w+)", adb.shell("dumpsys bluetooth_manager", timeout=60)),
         expect=lambda b, a: b == "true" and a == "false"),
    # Stored value is `global.mobile_data`; the OS probe is the radio data-connection state (2 connected, 0 not).
    Spec("mobiledata", "Mobile data", "DL-0081/0082", [],
         custom_get=lambda: adb.settings_get("global", "mobile_data"),
         custom_put=lambda v: adb.shell("svc data " + ("enable" if v == "1" else "disable")),
         test_value="0",
         probe=lambda: _m(r"mDataConnectionState=(\d)", adb.shell("dumpsys telephony.registry", timeout=60)),
         expect=lambda b, a: b == "2" and a == "0", settle_s=9.0),
    Spec("wifi", "Wi-Fi", "DL-0573/0574", [],
         custom_get=lambda: _m(r"Wifi is (\w+)", adb.shell("cmd wifi status", timeout=30)),
         custom_put=lambda v: adb.shell(f"cmd wifi set-wifi-enabled {v}"), test_value="enabled",
         probe=lambda: _m(r"Wifi is (\w+)", adb.shell("cmd wifi status", timeout=30)),
         expect=lambda b, a: b != a and a is not None),
]}


def run(spec: Spec) -> dict:
    r = {"id": spec.id, "label": spec.label, "catalog": spec.catalog, "verdict": "FAIL", "note": ""}
    s1 = snap(); time.sleep(1); s2 = snap()
    noise = (moved(s1, s2) | ALWAYS_NOISE)
    probe_before = spec.probe() if spec.probe else None
    originals: dict[str, str | None] = {}
    wrote = False
    try:
        if spec.custom_get:
            originals["custom"] = spec.custom_get(); r["original"] = originals["custom"]
            r["readable"] = originals["custom"] is not None
            tv = spec.test_value(originals["custom"]) if callable(spec.test_value) else spec.test_value
            spec.custom_put(tv); wrote = True
            time.sleep(spec.settle_s)
            r["after_write"] = spec.custom_get()
            r["writable"] = r["after_write"] not in (None, originals["custom"])
        else:
            for ns, key, _ in spec.writes:
                v = adb.settings_get(ns, key)
                originals[f"{ns}.{key}"] = v if v is not None else spec.treat_unset_as.get(f"{ns}.{key}")
            r["original"] = {k: v for k, v in originals.items()}
            r["readable"] = all(v is not None for v in originals.values())
            if not r["readable"]:
                r.update(verdict="SKIP", note="a key is unset and has no declared default; cannot restore without deleting"); return r
            for ns, key, val in spec.writes:
                adb.settings_put(ns, key, val); wrote = True
            time.sleep(spec.settle_s)
            r["after_write"] = {f"{ns}.{key}": adb.settings_get(ns, key) for ns, key, _ in spec.writes}
            r["writable"] = all(r["after_write"][f"{ns}.{key}"] == val for ns, key, val in spec.writes)
        probe_after = spec.probe() if spec.probe else None
        if spec.probe:
            r["probe"] = f"{probe_before} -> {probe_after}"
            r["effect"] = "observed" if spec.expect(probe_before, probe_after) else "NOT-OBSERVED"
        else:
            r["effect"] = "stored-only"
        s_applied = snap()
    finally:
        if wrote:
            try:
                if spec.custom_get:
                    spec.custom_put(originals["custom"])
                else:
                    for ns, key, _ in spec.writes:
                        adb.settings_put(ns, key, originals[f"{ns}.{key}"])
            except Exception as e:  # noqa: BLE001
                r["restore_error"] = f"{type(e).__name__}: {e}"
            time.sleep(spec.settle_s)
    s_after = snap()
    r["side_effect_keys"] = sorted(k for k in moved(s2, s_applied) - noise
                                   if k not in {f"{ns}.{key}" for ns, key, _ in spec.writes})[:20]
    leftover_all = sorted(moved(s2, s_after) - noise)
    r["benign_counters_changed"] = [k for k in leftover_all if k in BENIGN_COUNTERS]
    leftover = [k for k in leftover_all if k not in BENIGN_COUNTERS and not (k in spec.treat_unset_as and s_after.get(k) == spec.treat_unset_as[k])]
    conn = [k for k in leftover if spec.connection_keys and re.search(spec.connection_keys, k)]
    r["connection_state_keys_changed"] = conn
    leftover = [k for k in leftover if k not in conn]
    r["unrestored_keys"] = leftover[:20]
    if spec.custom_get:
        r["restore_confirmed"] = spec.custom_get() == originals["custom"]
    else:
        r["restore_confirmed"] = all((adb.settings_get(ns, key) == originals[f"{ns}.{key}"]) for ns, key, _ in spec.writes)
    r["namespace_identical_after_restore"] = not leftover
    ok = r.get("writable") and r["restore_confirmed"] and not leftover and "restore_error" not in r
    r["verdict"] = ("PASS-CAVEAT" if conn else "PASS") if ok else "FAIL"
    # NO-EFFECT first, then expected_negative. The other order never fired: an evidence row
    # whose cycle is clean scores PASS, which matched neither branch of the old if/elif, so
    # dnd_key reported NO-EFFECT while FINDINGS_2.md recorded it as XFAIL.
    if ok and r.get("effect") == "NOT-OBSERVED":
        r["verdict"] = "NO-EFFECT"   # clean cycle, but Android did not apply the write (or the probe is wrong): do not act on it
    notes = []
    if spec.expected_negative and r["verdict"] in ("FAIL", "NO-EFFECT"):
        r["verdict"] = "XFAIL"
        notes.append("expected: Android ignores or reverts a direct write to this key; use the cmd path (kept as evidence)")
    if conn:
        notes.append("radio restored and verified; keys owned by the connected device and by Samsung Modes (ringer mode, volumes) changed when the earbuds disconnected and are not ours to rewrite; reconnect the device and check the ringer mode")
    # Only when a key really was unset, not merely because the spec declares a default.
    was_unset = [k for k in spec.treat_unset_as if k not in s2]
    if was_unset:
        notes.append(f"{', '.join(was_unset)} was unset before; restored to its documented default, so it now exists as an explicit value")
    if notes:
        r["note"] = " | ".join(notes)   # appended, never overwritten: the Bluetooth warning must survive
    return r


def main() -> int:
    if not adb.device_connected():
        print("no authorised device"); return 1
    ids = [a for a in sys.argv[1:] if not a.startswith("--")] or list(SPECS)
    results = []
    for i in ids:
        try:
            results.append(run(SPECS[i]))
        except Exception as e:  # noqa: BLE001 - an adb failure is a result, not a crash
            results.append({"id": i, "label": SPECS[i].label, "catalog": SPECS[i].catalog, "verdict": "FAIL",
                            "note": f"{type(e).__name__}: {e}"[:200], "side_effect_keys": [], "unrestored_keys": []})
        x = results[-1]
        print(f"{x['verdict']:5} {x['id']:<11} effect={x.get('effect','-'):<12} probe={x.get('probe','-')} "
              f"side_effects={len(x['side_effect_keys'])} unrestored={len(x['unrestored_keys'])} {x['note']}", flush=True)
    OUT.mkdir(exist_ok=True)
    path = OUT / "matrix_results.json"
    merged = {r["id"]: r for r in (json.loads(path.read_text()) if path.exists() else [])}
    merged.update({r["id"]: r for r in results})          # keep earlier runs; latest result per id wins
    path.write_text(json.dumps(list(merged.values()), indent=2, default=str))
    accepted = ("PASS", "PASS-CAVEAT", "NO-EFFECT", "SKIP", "XFAIL")
    return 0 if all(r["verdict"] in accepted for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
