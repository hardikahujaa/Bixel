"""Score out/baseline.json and out/laya.json:  python -m tools.laya_spike.compare

Generate the two inputs first (see the commands in the error message below); both land in
out/, which is gitignored because the rows carry model output.

An abstention is scored as a **refusal, never as an action**. Laya omits "choice" when it
declines, which left `pred` as None; `None != "none"` then counted the row in
acted-on-off-topic -- the one column the implementation plan treats as a hard gate of zero --
so a model that correctly declined was scored as though it had touched a setting.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).parent / "out"

_HOWTO = (
    "  python -m tools.laya_spike.baseline                     # writes out/baseline.json\n"
    "  <laya-venv>/python tools/laya_spike/laya_run.py         # writes out/laya.json"
)


def load(name: str) -> list[dict]:
    path = OUT / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{path} is missing. Generate it first:\n{_HOWTO}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not rows:
        raise SystemExit(f"{path} is empty; re-run the step that writes it:\n{_HOWTO}")
    for r in rows:
        # No choice returned == declined to act. Fold it into the explicit "none" class so
        # every column below counts a refusal as a refusal.
        r["pred"] = r.get("pred") or "none"
    return rows


def rep(name: str, rows: list[dict]) -> None:
    n = len(rows)
    neg = [r for r in rows if r["label"] == "none"]
    pos = [r for r in rows if r["label"] != "none"]
    print(
        f"{name:9} acc={sum(r['pred'] == r['label'] for r in rows) / n:.3f}"
        f"  acted-on-off-topic={sum(r['pred'] != 'none' for r in neg)}/{len(neg)}"
        f"  refused-real={sum(r['pred'] == 'none' for r in pos)}/{len(pos)}"
        f"  wrong-class={sum(r['pred'] not in ('none', r['label']) for r in pos)}"
        f"  abstained={sum(bool(r.get('abstained')) for r in rows)}/{n}"
        f"  median={sorted(r['ms'] for r in rows)[n // 2]}ms"
    )


def main() -> int:
    baseline, laya = load("baseline"), load("laya")
    rep("baseline", baseline)
    rep("laya", laya)
    for name, rows, field in (("laya", laya, "conf"), ("baseline", baseline, "score")):
        print(f"{name} errors:")
        for r in rows:
            if r["pred"] != r["label"]:
                print(f"   {r['label']:>19} -> {r['pred']:<19} "
                      f"{round(float(r.get(field) or 0), 2)} | {r['complaint'][:68]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
