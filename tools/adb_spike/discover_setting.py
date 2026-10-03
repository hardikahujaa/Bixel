"""Find the real settings key behind a toggle by diffing `settings list` snapshots.

Workflow (the human flips ONE toggle between `after` and `before`):

    python discover_setting.py before  <label>     # 2 snapshots a few seconds apart -> noise filter
    #   ... flip exactly one toggle on the phone ...
    python discover_setting.py after   <label>
    python discover_setting.py diff    <label>     # prints only the keys that changed because of the flip

Snapshots are raw personal data from a real phone: they go to tools/adb_spike/dumps/ (gitignored)
and are never printed in full - `diff` shows only the changed keys.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import adb  # noqa: E402

DUMPS = Path(__file__).parent / "dumps"


def snapshot() -> dict[str, dict[str, str]]:
    return {ns: adb.settings_list(ns) for ns in adb.NAMESPACES}


def changed(a: dict, b: dict) -> dict[str, tuple[str | None, str | None]]:
    out = {}
    for ns in adb.NAMESPACES:
        for k in set(a[ns]) | set(b[ns]):
            if a[ns].get(k) != b[ns].get(k):
                out[f"{ns}.{k}"] = (a[ns].get(k), b[ns].get(k))
    return out


def _save(name: str, data) -> None:
    DUMPS.mkdir(exist_ok=True)
    (DUMPS / f"{name}.json").write_text(json.dumps(data))


def _load(name: str):
    return json.loads((DUMPS / f"{name}.json").read_text())


def cmd_before(label: str) -> None:
    first = snapshot()
    time.sleep(6)
    second = snapshot()
    noise = sorted(changed(first, second) | {"system.settings_change_history": 0})  # self-moving keys + Android's own change log
    _save(f"{label}.before", second)
    _save(f"{label}.noise", noise)
    print(f"baseline saved; {len(noise)} self-changing keys will be ignored in the diff")


def cmd_after(label: str) -> None:
    _save(f"{label}.after", snapshot())
    print("after-snapshot saved")


def cmd_diff(label: str) -> None:
    noise = set(_load(f"{label}.noise")) | {"system.settings_change_history"}
    diff = {k: v for k, v in changed(_load(f"{label}.before"), _load(f"{label}.after")).items()
            if k not in noise}
    if not diff:
        print("no key changed - the toggle may live outside `settings` (e.g. a Samsung provider/service)")
    for k, (old, new) in sorted(diff.items()):
        print(f"{k}: {old!r} -> {new!r}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("before", "after", "diff"):
        sys.exit(__doc__)
    {"before": cmd_before, "after": cmd_after, "diff": cmd_diff}[sys.argv[1]](sys.argv[2])
