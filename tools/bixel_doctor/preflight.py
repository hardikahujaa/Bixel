"""Run right before filming:  python -m tools.bixel_doctor.preflight

Checks that the phone is connected and authorised, that adb can actually write settings (which is
what Auto Blocker's "Block commands by USB cable" would stop), and that the three flagship
settings still read, write, take effect in the OS, and restore. Every write is restored in a
``finally`` block. Prints READY or NOT READY; exit code 0 only for READY.

Auto Blocker's own switch is NOT exposed as any `settings` key, so it cannot be read over adb. It is
reported as MANUAL: confirm by eye. What the script CAN prove is the consequence -- if the
write/read-back cycle below passes, adb commands are not being blocked.
"""
from __future__ import annotations

import re
import subprocess
import sys

from tools.adb_spike import adb

from .controls import CONTROLS, Control

OK, BAD, WARN, MAN = "PASS", "FAIL", "WARN", "MANUAL"


def check_device() -> tuple[str, str]:
    try:
        out = subprocess.run([adb.ADB, "devices"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return BAD, f"cannot run adb at {adb.ADB}: {e}"
    rows = [l.split() for l in out.strip().splitlines()[1:] if l.strip()]
    if not rows:
        return BAD, "no device listed. Plug in the cable, unlock the phone, and check USB debugging is on"
    if len(rows) > 1:
        return BAD, f"{len(rows)} devices attached; unplug the others so adb is unambiguous"
    state = rows[0][-1]
    if state == "unauthorized":
        return BAD, "phone shows UNAUTHORIZED: unlock it and tap Allow on the 'Allow USB debugging?' popup (tick 'Always allow')"
    if state != "device":
        return BAD, f"device state is '{state}', expected 'device'. Re-plug the cable"
    return OK, "connected and authorised (serial deliberately not printed)"


def check_auth_window() -> tuple[str, str]:
    ms = adb.settings_get("global", "adb_allowed_connection_time")
    if not ms:
        return WARN, "could not read the USB-debugging authorisation window"
    try:
        days = int(ms) / 86_400_000
    except ValueError:
        return WARN, f"unexpected authorisation-window value {ms!r}"
    return WARN if days > 0 else OK, (
        f"authorisation expires after {days:.0f} days without a connection; "
        "if the phone has sat unplugged longer, expect the Allow popup again")


def check_awake() -> tuple[str, str]:
    m = re.search(r"mWakefulness=(\w+)", adb.shell("dumpsys power", timeout=30))
    state = m.group(1) if m else "unknown"
    return (OK, "screen is awake") if state == "Awake" else (WARN, f"screen is {state}; wake and unlock it before filming")


def cycle(ctl: Control, test_value: str) -> tuple[str, str]:
    before = ctl.snapshot()
    if before["os"] is None:
        return BAD, "cannot read OS state"
    want = ctl.expected_os(test_value)
    if before["os"] == want:
        return BAD, f"test value equals current OS state ({want}); pick another value"
    restore_error = None
    try:
        ctl.apply(test_value)
        seen = ctl.settle(want)
    finally:
        try:
            ctl.restore(before["stored"], before["os"])
            ctl.settle(before["os"])
        except Exception as e:  # noqa: BLE001 - must not mask the original error or hide an unrestored phone
            restore_error = e
    if restore_error is not None:
        return BAD, f"RESTORE NOT CONFIRMED ({type(restore_error).__name__}: {restore_error}); check the phone's setting by hand"
    after = ctl.snapshot()
    restored = after["os"] == before["os"] and (after["stored"] == before["stored"] or ctl.key == "Dark mode settings")
    if seen != want:
        return BAD, f"OS reported {seen!r}, expected {want!r}" + ("" if restored else " AND RESTORE NOT CONFIRMED")
    return (OK, f"write took effect ({before['os']} -> {seen}) and restore confirmed") if restored else (
        BAD, f"RESTORE NOT CONFIRMED: {before} -> {after}")


def pick_values() -> dict[str, str]:
    t = CONTROLS["Screen timeout"].read_stored()
    d = CONTROLS["Dark mode settings"].read_os()
    b = CONTROLS["Adaptive brightness"].read_stored()
    return {
        "Adaptive brightness": "1" if b == "0" else "0",
        "Screen timeout": "120000" if t == "300000" else "300000",
        "Dark mode settings": "yes" if d == "no" else "no",
    }


def main() -> int:
    results: list[tuple[str, str, str]] = []
    status, msg = check_device()
    results.append(("device connected + authorised", status, msg))
    if status == OK:
        for name, fn in (("USB-debugging authorisation window", check_auth_window), ("screen awake", check_awake)):
            try:
                results.append((name, *fn()))
            except Exception as e:  # noqa: BLE001
                results.append((name, WARN, f"check failed: {type(e).__name__}: {e}"))
        results.append(("Auto Blocker off", MAN, "cannot be read over adb: open Settings > Security and privacy > Auto Blocker and confirm it is OFF"))
        try:
            values = pick_values()
        except Exception as e:  # noqa: BLE001 - a cable drop here must not end in a traceback
            results.append(("read current values", BAD, f"{type(e).__name__}: {e}"))
            values = {}
        for key, ctl in (CONTROLS.items() if values else ()):
            try:
                results.append((f"{ctl.label}: read/write/OS-effect/restore", *cycle(ctl, values[key])))
            except Exception as e:  # noqa: BLE001
                results.append((f"{ctl.label}: read/write/OS-effect/restore", BAD, f"{type(e).__name__}: {e}"))
    width = max(len(n) for n, _, _ in results)
    for name, st, m in results:
        print(f"{st:6} {name:<{width}}  {m}")
    ready = all(st != BAD for _, st, _ in results)
    print("\nREADY to film" if ready else "\nNOT READY - fix the FAIL lines above")
    if ready and any(st == MAN for _, st, _ in results):
        print("(MANUAL items are unconfirmed by the script: check them by eye.)")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
