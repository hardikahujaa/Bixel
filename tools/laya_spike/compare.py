"""Score out/baseline.json and out/laya.json:  python tools/laya_spike/compare.py"""
import json
from pathlib import Path
O = Path(__file__).parent / "out"
B, L = json.loads((O / "baseline.json").read_text()), json.loads((O / "laya.json").read_text())

def rep(name, rows):
    n = len(rows); neg = [r for r in rows if r["label"] == "none"]; pos = [r for r in rows if r["label"] != "none"]
    print(f"{name:9} acc={sum(r['pred']==r['label'] for r in rows)/n:.3f}  acted-on-off-topic={sum(r['pred']!='none' for r in neg)}/{len(neg)}"
          f"  refused-real={sum(r['pred']=='none' for r in pos)}/{len(pos)}  wrong-class={sum(r['pred'] not in ('none', r['label']) for r in pos)}"
          f"  median={sorted(r['ms'] for r in rows)[n//2]}ms")

rep("baseline", B); rep("laya", L)
for name, rows, f in (("laya", L, "conf"), ("baseline", B, "score")):
    print(f"{name} errors:")
    for r in rows:
        if r["pred"] != r["label"]:
            print(f"   {r['label']:>19} -> {r['pred']:<19} {round(float(r[f] or 0), 2)} | {r['complaint'][:68]}")
