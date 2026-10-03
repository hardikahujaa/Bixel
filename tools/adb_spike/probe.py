"""READ-ONLY probe of the connected phone. Changes nothing. Prints JSON with units.

    python tools/adb_spike/probe.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Path(), not __file__.rsplit("/"): on Windows __file__ has no forward slash, so rsplit
# returned the whole file path and `import adb` failed for every caller but a direct
# `python probe.py` (which gets this directory on sys.path anyway).
sys.path.insert(0, str(Path(__file__).parent))
import adb  # noqa: E402

CHARGE_STATUS = {1: "unknown", 2: "charging", 3: "discharging", 4: "not_charging", 5: "full"}
NIGHT_MODE = {0: "auto/unset", 1: "off", 2: "on"}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def battery() -> dict:
    # Read the BatteryService block only; `dumpsys battery` is prefixed by log lines.
    kv = {}
    for line in adb.shell("dumpsys battery").splitlines():
        m = re.match(r"^\s{2}([A-Za-z ]+): (.+)$", line)
        if m:
            kv[m.group(1).strip()] = m.group(2).strip()
    status = _int(kv.get("status"))
    temp = _int(kv.get("temperature"))
    return {
        "level_percent": _int(kv.get("level")),
        # Tenths of a degree. None, not 0.0, when the field is unreadable: a missing sensor
        # must not be reported as a phone at freezing point.
        "temperature_celsius": temp / 10 if temp is not None else None,
        "charging_state": CHARGE_STATUS.get(status, str(status)),
        "plugged_via": [k.split()[0].lower() for k in ("AC powered", "USB powered", "Wireless powered")
                        if kv.get(k) == "true"] or ["none"],
    }


def _uid_to_package() -> dict[int, str]:
    out = {}
    for line in adb.shell("cmd package list packages -U", timeout=60).splitlines():
        m = re.match(r"package:(\S+) uid:(\d+)", line)
        if m:
            out.setdefault(int(m.group(2)), m.group(1))
    return out


def _uid_label(token: str, pkgs: dict[int, str]) -> str:
    """batterystats prints u0a348 for uid 10348; system uids are numeric/negative."""
    m = re.fullmatch(r"u0a(\d+)", token)
    if m:
        return pkgs.get(10000 + int(m.group(1)), f"unresolved({token})")
    return {"-5": "android:system-overhead", "1000": "android:system", "0": "android:kernel/root"}.get(
        token, f"system-or-other-user({token})")


def top_battery_apps(n: int = 5) -> list[dict]:
    text = adb.shell("dumpsys batterystats", timeout=120)
    start = text.find("Estimated power use (mAh)")
    if start < 0:
        return []
    pkgs = _uid_to_package()
    apps = []
    for line in text[start:].splitlines()[1:200]:
        m = re.match(r"^\s{2}UID (\S+): ([\d.]+)", line)
        if m:
            apps.append({"app": _uid_label(m.group(1), pkgs), "drain_mah_since_last_charge": float(m.group(2))})
        elif apps and not line.strip():
            break
    apps = [a for a in apps if not a["app"].startswith("android:")]  # user-meaningful only
    return sorted(apps, key=lambda a: -a["drain_mah_since_last_charge"])[:n]


def top_cpu_apps(n: int = 5) -> list[dict]:
    out = []
    for line in adb.shell("dumpsys cpuinfo", timeout=60).splitlines():
        m = re.match(r"^\s*([\d.]+)% \d+/(\S+?):", line)
        if m and "." in m.group(2) and not m.group(2).startswith("kworker"):
            out.append({"process": m.group(2), "cpu_percent": float(m.group(1))})
    return sorted(out, key=lambda r: -r["cpu_percent"])[:n]


def collect() -> dict:
    night = _int(adb.settings_get("secure", "ui_night_mode"))
    brightness_mode = _int(adb.settings_get("system", "screen_brightness_mode"))
    timeout_ms = _int(adb.settings_get("system", "screen_off_timeout"))
    oneui = _int(adb.getprop("ro.build.version.oneui"))
    result = {
        "device": {
            "model": adb.getprop("ro.product.model"),
            "android_version": adb.getprop("ro.build.version.release"),
            "android_sdk": _int(adb.getprop("ro.build.version.sdk")),
            "one_ui_version": f"{oneui // 10000}.{(oneui % 10000) // 100}" if oneui else None,
        },
        "battery": battery(),
        "display": {
            "adaptive_brightness": {1: "on", 0: "off"}.get(brightness_mode, "unknown"),
            "screen_timeout_seconds": timeout_ms / 1000 if timeout_ms else None,
            "dark_mode": NIGHT_MODE.get(night, "unknown"),
            # Found by discover_setting.py: secure.refresh_rate_mode, 1 = Adaptive, 0 = Standard
            "motion_smoothness": {"1": "adaptive", "0": "standard"}.get(
                adb.settings_get("secure", "refresh_rate_mode"), "unknown"),
        },
        "top_battery_apps": top_battery_apps(),
        "top_cpu_processes_now": top_cpu_apps(),
    }
    return result


def main() -> int:
    try:
        if not adb.device_connected():
            print(json.dumps({"error": "no authorised device - check cable and USB-debugging prompt"}))
            return 1
        print(json.dumps(collect(), indent=2))
    except adb.AdbError as e:
        # The whole point of this script is a readable report; a traceback is not one.
        print(json.dumps({"error": str(e)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
