"""Baseline: our existing selector (embedding similarity + floor) on the authored complaints.
Run from the repo root with the repo .venv:  .venv/bin/python -m tools.laya_spike.baseline
Writes tools/laya_spike/out/baseline.json (gitignored). No phone needed."""
import json, time
from pathlib import Path
from tools.bixel_doctor.doctor import pick_document

HERE = Path(__file__).parent
TITLE_TO_CLASS = {"Screen brightness keeps changing": "adaptive_brightness",
                  "Screen turns off too quickly": "screen_timeout",
                  "Screen is too dark to read": "dark_mode"}

def main():
    rows = json.loads((HERE / "complaints.json").read_text())["rows"]
    pick_document("warm up")
    out = []
    for r in rows:
        t0 = time.perf_counter(); title, sim = pick_document(r["complaint"]); ms = (time.perf_counter() - t0) * 1000
        pred = next((c for p, c in TITLE_TO_CLASS.items() if title and title.startswith(p)), "none")
        out.append({**r, "pred": pred, "score": round(sim, 3), "ms": round(ms, 1)})
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "baseline.json").write_text(json.dumps(out, indent=1))
    print(f"baseline written ({len(out)} rows)")

if __name__ == "__main__":
    main()
