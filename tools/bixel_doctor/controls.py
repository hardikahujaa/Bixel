"""The only three things the Doctor is allowed to change on the phone.

Each control knows four things: how to read the *stored* value, how to read the *OS state*
(what Android is actually doing, which is not always the same thing -- see dark mode), how
to apply a value, and how to put the original back. A control is keyed by the catalog's
``validation.key`` so that catalog selection and phone action can never drift apart.

Deliberately NOT here: touch sensitivity and motion smoothness. The spike proved only that
their stored value changes, not that the phone's behaviour does, so the Doctor reads them
(``read_only_diagnostics``) and never writes them. Dark mode is applied ONLY through
``cmd uimode night``; writing ``secure.ui_night_mode`` is accepted by Android and ignored.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

from tools.adb_spike import adb

# Samsung's Screen timeout menu offers these; refuse anything else rather than invent a value.
TIMEOUT_CHOICES_MS = {15_000, 30_000, 60_000, 120_000, 300_000, 600_000, 1_800_000}


def _dumpsys_field(service: str, pattern: str) -> str | None:
    m = re.search(pattern, adb.shell(f"dumpsys {service}", timeout=60))
    return m.group(1) if m else None


def _uimode() -> str | None:
    m = re.search(r"Night mode: (\w+)", adb.shell("cmd uimode night"))
    return m.group(1) if m else None


@dataclass(frozen=True)
class Control:
    key: str                       # catalog validation.key this control serves
    label: str
    terms: re.Pattern              # the step text must actually name the setting
    directional: bool              # True: catalog has Enable/Disable pairs, direction comes from polarity
    read_stored: Callable[[], str | None]
    read_os: Callable[[], str | None]
    apply: Callable[[str], str]    # returns the exact command that was run (for the report)
    restore: Callable[[str | None, str | None], None]  # (stored, os) originals
    expected_os: Callable[[str], str]   # OS state we must observe after applying `value`

    def snapshot(self) -> dict:
        return {"stored": self.read_stored(), "os": self.read_os()}

    def settle(self, want_os: str, timeout_s: float = 5.0) -> str | None:
        """Poll until the OS reports the wanted state (it can lag the stored value by a moment)."""
        deadline = time.time() + timeout_s
        seen = self.read_os()
        while seen != want_os and time.time() < deadline:
            time.sleep(0.4)
            seen = self.read_os()
        return seen


def _put(ns: str, key: str, value: str) -> str:
    adb.settings_put(ns, key, value)
    return f"settings put {ns} {key} {value}"


CONTROLS: dict[str, Control] = {
    "Adaptive brightness": Control(
        key="Adaptive brightness", label="Adaptive brightness",
        terms=re.compile(r"adaptive\s+brightness", re.I), directional=True,
        read_stored=lambda: adb.settings_get("system", "screen_brightness_mode"),   # 1 on, 0 off
        read_os=lambda: _dumpsys_field("display", r"mUseAutoBrightness=(\w+)"),     # true / false
        apply=lambda v: _put("system", "screen_brightness_mode", v),
        restore=lambda stored, _os: _put("system", "screen_brightness_mode", stored) if stored else None,
        expected_os=lambda v: "true" if v == "1" else "false",
    ),
    "Screen timeout": Control(
        key="Screen timeout", label="Screen timeout",
        terms=re.compile(r"screen\s+timeout", re.I), directional=False,
        read_stored=lambda: adb.settings_get("system", "screen_off_timeout"),       # milliseconds
        read_os=lambda: _dumpsys_field("power", r"mScreenOffTimeoutSetting=(\d+)"),
        apply=lambda v: _put("system", "screen_off_timeout", v),
        restore=lambda stored, _os: _put("system", "screen_off_timeout", stored) if stored else None,
        expected_os=lambda v: v,
    ),
    "Dark mode settings": Control(
        key="Dark mode settings", label="Dark mode",
        terms=re.compile(r"dark\s+mode", re.I), directional=True,
        read_stored=lambda: adb.settings_get("secure", "ui_night_mode"),            # informational only
        read_os=_uimode,                                                             # yes / no / auto
        apply=lambda v: (adb.shell(f"cmd uimode night {v}"), f"cmd uimode night {v}")[1],  # v in yes|no
        restore=lambda _stored, os_state: adb.shell(f"cmd uimode night {os_state}") if os_state else None,
        expected_os=lambda v: v,
    ),
}


def read_only_diagnostics() -> dict:
    """Mentioned in the report, never written. Stored values only -- see module docstring."""
    smooth = adb.settings_get("secure", "refresh_rate_mode")
    touch = adb.settings_get("system", "auto_adjust_touch")
    return {
        "motion_smoothness": {"1": "adaptive", "0": "standard"}.get(smooth, "unknown"),
        "touch_sensitivity": {"1": "on", "0": "off"}.get(touch, "unknown"),
        "note": "read-only: the spike proved the stored value moves, not that the phone's behaviour does",
    }
