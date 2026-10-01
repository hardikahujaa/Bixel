"""Thin, safety-guarded wrapper around the adb binary for the spike.

Rules enforced here, not just promised in prose:
  * the device serial is never hard-coded or printed (adb picks the sole device);
  * anything destructive (reset, uninstall, wipe, root, rm, pm clear ...) is refused;
  * settings writes are only reachable through ``settings_put`` and are meant to be
    called from a read -> write -> read-back -> restore -> read-back cycle.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ADB = os.environ.get("ADB", str(Path.home() / "platform-tools" / "adb"))
NAMESPACES = ("system", "secure", "global")

# Words that must never appear in a command we send to the phone.
_FORBIDDEN = re.compile(
    r"\b(rm|rmdir|wipe|reset|factory|uninstall|su|sudo|reboot|format|dd|mkfs|"
    r"pm\s+(clear|uninstall|disable|disable-user)|cmd\s+package\s+(uninstall|clear)|"
    r"recovery|bootloader|flash|delete|settings\s+delete)\b",
    re.IGNORECASE,
)


class AdbError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 60) -> str:
    try:
        p = subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise AdbError(f"adb timed out: {args[:3]}") from e
    if p.returncode != 0:
        raise AdbError(f"adb {' '.join(args[:3])} failed: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def shell(cmd: str, timeout: int = 60) -> str:
    if _FORBIDDEN.search(cmd):
        raise AdbError(f"refused: command not allowed in the spike: {cmd!r}")
    return _run(["shell", cmd], timeout=timeout)


def device_connected() -> bool:
    lines = _run(["devices"]).strip().splitlines()[1:]
    return any(l.split()[-1] == "device" for l in lines if l.strip())


def getprop(name: str) -> str | None:
    v = shell(f"getprop {name}").strip()
    return v or None


def settings_get(ns: str, key: str) -> str | None:
    """Return the value, or None when the key is unset/absent (adb prints 'null')."""
    assert ns in NAMESPACES, ns
    v = shell(f"settings get {ns} {key}").strip()
    return None if v in ("", "null") else v


def settings_put(ns: str, key: str, value: str) -> None:
    assert ns in NAMESPACES, ns
    assert re.fullmatch(r"[A-Za-z0-9_.\-]+", key), key
    assert re.fullmatch(r"[A-Za-z0-9_.\-]+", str(value)), value
    shell(f"settings put {ns} {key} {value}")


def settings_list(ns: str) -> dict[str, str]:
    assert ns in NAMESPACES, ns
    out: dict[str, str] = {}
    for line in shell(f"settings list {ns}", timeout=120).splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out
