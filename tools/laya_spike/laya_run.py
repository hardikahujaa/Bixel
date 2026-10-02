"""Laya on the same complaints. Run with the isolated venv that has `laya` + torch (NOT the repo .venv):
    <laya_venv>/bin/python tools/laya_spike/laya_run.py [min_confidence]
Writes tools/laya_spike/out/laya.json (gitignored). No phone needed."""
import json, sys, time
from pathlib import Path
from laya import Router

HERE = Path(__file__).parent
CRITERIA = {
    "adaptive_brightness": "the screen brightness changes, dims or brightens by itself automatically",
    "screen_timeout": "the screen turns off or locks too quickly, or stays on too long",
    "dark_mode": "the dark theme or black interface makes things hard to read",
    "none": "the complaint is about something else, not brightness, screen timeout or dark theme",
}
Q = {"setting": {"type": "choice", "instructions": "Which phone display setting would fix this complaint?", "criteria": CRITERIA}}

def main():
    min_conf = float(sys.argv[1]) if len(sys.argv) > 1 else None
    rows = json.loads((HERE / "complaints.json").read_text())["rows"]
    router = Router()
    router.predict({"body": "warm up"}, Q)
    out = []
    for r in rows:
        t0 = time.perf_counter()
        res = router.predict({"body": r["complaint"]}, Q, **({"min_confidence": min_conf} if min_conf else {}))
        ms = (time.perf_counter() - t0) * 1000
        a = res["answers"]["setting"]
        out.append({**r, "pred": a.get("choice"), "conf": a.get("answer_confidence", a.get("confidence")),
                    "abstained": a.get("low_confidence", False), "probs": a.get("probabilities"), "ms": round(ms, 1)})
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "laya.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"laya written ({len(out)} rows)")

if __name__ == "__main__":
    main()
