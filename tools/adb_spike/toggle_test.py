"""Safe read -> write -> read-back -> restore -> read-back cycle for five real settings (dark mode is tried via two write paths).

    python tools/adb_spike/toggle_test.py [--json]

Each setting is tested on its own and reported PASS/FAIL (XFAIL = a write Android is expected to ignore, kept as evidence). The original value is restored in a
``finally`` block, so a failure part-way through cannot leave the phone changed. Nothing is
ever deleted; a setting that is currently unset is SKIPped because it could not be restored.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import adb  # noqa: E402


def _uimode_night() -> str | None:
    m = re.search(r"Night mode: (\w+)", adb.shell("cmd uimode night"))
    return m.group(1) if m else None


def _power_timeout() -> str | None:
    m = re.search(r"mScreenOffTimeoutSetting=(\d+)", adb.shell("dumpsys power", timeout=60))
    return m.group(1) if m else None


def _use_auto_brightness() -> str | None:
    m = re.search(r"mUseAutoBrightness=(\w+)", adb.shell("dumpsys display", timeout=60))
    return m.group(1) if m else None


@dataclass
class Spec:
    name: str
    ns: str
    key: str
    test_value: str
    catalog_query: str  # plain-language phrase, used by FINDINGS to run our matcher
    effect_probe: object = None  # optional: callable returning an observable (proves Android applied it)
    expect_fail: bool = False  # kept as evidence: a write we expect Android to ignore -> XFAIL, not FAIL
    getter: object = None  # optional override of how the value is read (default: `settings get`)
    putter: object = None  # optional override of how it is written (default: `settings put`)


SPECS = [
    Spec("adaptive brightness", "system", "screen_brightness_mode", "0", "turn off adaptive brightness", effect_probe=_use_auto_brightness),
    Spec("motion smoothness / refresh rate", "secure", "refresh_rate_mode", "0", "change motion smoothness refresh rate"),
    Spec("screen timeout", "system", "screen_off_timeout", "60000", "change screen timeout", effect_probe=_power_timeout),
    Spec("dark mode", "secure", "ui_night_mode", "1", "turn off dark mode", effect_probe=_uimode_night, expect_fail=True),
    Spec("dark mode (via cmd uimode)", "secure", "ui_night_mode", "no", "turn off dark mode",
         effect_probe=_uimode_night, getter=_uimode_night,
         putter=lambda v: adb.shell(f"cmd uimode night {v}")),
    Spec("touch sensitivity", "system", "auto_adjust_touch", "0", "turn on touch sensitivity"),
]


def run_cycle(s: Spec) -> dict:
    r = {k: v for k, v in asdict(s).items() if k not in ("effect_probe", "getter", "putter", "expect_fail")}
    r.update(readable=False, writable=False, restore_confirmed=False, effect_observed=None, verdict="FAIL", note="")
    get = s.getter or (lambda: adb.settings_get(s.ns, s.key))
    put = s.putter or (lambda v: adb.settings_put(s.ns, s.key, v))
    original = get()
    r["original"] = original
    r["readable"] = original is not None
    if original is None:
        r.update(verdict="SKIP", note="key is unset; cannot restore without deleting, which is forbidden")
        return r
    if original == s.test_value:
        r.update(verdict="SKIP", note="test value equals current value; nothing to prove")
        return r
    before_effect = s.effect_probe() if s.effect_probe else None
    try:
        put(s.test_value)
        time.sleep(1.0)
        r["after_write"] = get()
        r["writable"] = r["after_write"] == s.test_value
        if s.effect_probe:
            after_effect = s.effect_probe()
            r["effect_observed"] = f"{before_effect} -> {after_effect}"
            if after_effect == before_effect:
                r["note"] = "key changed but Android did not apply it (no effect observed)"
    finally:
        put(original)
        time.sleep(1.0)
        r["after_restore"] = get()
        r["restore_confirmed"] = r["after_restore"] == original
    effect_ok = (not s.effect_probe) or (r["effect_observed"] and before_effect != r["effect_observed"].split(" -> ")[1])
    ok = bool(r["writable"] and r["restore_confirmed"] and effect_ok)
    r["verdict"] = ("XFAIL" if s.expect_fail else "PASS") if (ok != s.expect_fail) else "FAIL"
    r["effect_verified"] = bool(s.effect_probe and effect_ok)
    return r


def main() -> int:
    if not adb.device_connected():
        print("no authorised device")
        return 1
    results = [run_cycle(s) for s in SPECS]
    if "--json" in sys.argv:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{r['verdict']:5}  {r['ns']}.{r['key']:<22} {r['name']:<34} "
                  f"orig={r['original']} write={r.get('after_write')} restored={r.get('after_restore')} "
                  f"{('effect: ' + r['effect_observed']) if r['effect_observed'] else ''} {r['note']}")
    return 0 if all(r["verdict"] in ("PASS", "XFAIL", "SKIP") for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
