# `backend/matcher` — deeplink matcher (M1)

Maps a troubleshooting step to entries in Samsung's deeplink catalog, or to nothing.

**For M2 and M3: everything you need is in this file. You should not have to read the
implementation.**

---

## Using it

```python
from backend.matcher.matcher import match_deeplinks, MatchContext, get_matcher

# Warm at server startup. NEVER on the first request -- see "Cold start" below.
get_matcher()

candidates = match_deeplinks(
    step_text="5. Touch Sensitivity Setting. If the Touch sensitivity setting is "
              "enabled when you are not using a protective film... To turn off this "
              "feature, navigate to Settings, tap Display...",
    ctx=MatchContext(
        heading="5. Touch Sensitivity Setting",
        siis_title="Touchscreen issues on a Galaxy phone or tablet",
        query="My Galaxy S22 screen inputs are delayed",
    ),
)
# -> [MatchCandidate(catalog_id='DL-0125', score=0.81, entry={...}, polarity=-1, why='...')]
```

`MatchCandidate` fields:

| Field | Meaning |
|---|---|
| `catalog_id` | e.g. `DL-0125` |
| `score` | fused score, best first |
| `entry` | **the raw `deeplinks.json` entry, untouched** |
| `polarity` | `+1` enable, `-1` disable, `0` neutral |
| `why` | diagnostic string with the raw signals — worth logging |
| `signals` | `{"semantic": …, "lexical": …, "grounding": …}` |

### Two things to know before you integrate

**1. `[]` is the normal answer, not an error.** The catalog is a Settings-toggle
catalog. These SIIS documents mostly instruct physical actions, app navigation and
support escalation. There is **no** entry for safe mode, clearing cache, Smart Switch,
screen mirroring, factory reset or super steady mode. 30 of the 38 labelled groups
correctly match nothing. Do not treat an empty list as a failure, and do not lower the
threshold to manufacture coverage — an `auto` action with a wrong deeplink costs more
than a `manual` action with none.

**2. Pass group-level text, not single sentences.** Individual steps ("Tap Storage.",
"Navigate to Settings.") have no settings target. Pass the joined `##` section text as
`step_text` and put the heading in `ctx.heading`. The SIIS title and user query are
accepted but deliberately **excluded** from the match text — they describe the symptom,
not the action, and mixing them in drags matches toward whatever the document is
broadly about.

### For M2 (`to_deeplink_pair`)

`candidate.entry` is the raw catalog dict, with exactly these keys: `id`, `deeplink`,
`description`, `message`, `originalType`, `control_type`, `qna_description`,
`validation`. This module deliberately does **not** project it into the response
`Deeplink` / `ValidationDeepLink` shape — that rule lives only in your
`to_deeplink_pair()` so it cannot drift between two implementations. A test here pins
that the entry comes back unmodified.

---

## Measured performance

From `python -m backend.matcher.evaluate` on 38 hand-labelled SIIS groups
(5 must-match, 30 must-abstain, 3 arguable) plus 9 adversarial inputs:

```
precision 1.000   recall 0.800   f1 0.889   abstention accuracy 1.000
TP=4  FN=1  FP=0  TN=30          adversarial 9/9
selected gate: semantic >= 0.77, grounding >= 0.60, lexical >= 0.00
```

**Zero false positives across all 30 no-match cases**, including the two traps below.

**Read the recall figure honestly:** with 5 positives it moves in steps of 0.200. The
meaningful result is that precision holds at 1.000 across a *plateau* of gates
(semantic 0.76–0.79) while every no-match case abstains — not the third decimal place.
The one miss is "Use Multi window" → `DL-0168`, which scores 0.745; admitting it would
mean dropping the gate to 0.74, and that introduces a false positive. Re-run
`evaluate.py` after any change to the model, index, catalog or gate.

### Why two signals and not one

Both were measured on the real catalog, and **neither works alone**:

* **Lexical alone fails.** TF-IDF scores the false positive "7. Safe Mode" at **0.279**
  and the true positive "Update Device Software" at **0.166**. The distributions
  overlap.
* **Semantic alone fails.** The weakest true positive sits at **0.707**, the strongest
  false positive at **0.704** — a 0.003 gap.

The gate therefore requires semantic similarity **and** *grounding*: the words that
actually identify a setting must appear in the step. `lexical` ended up at 0.00 because
the sweep showed grounding subsumes it; it is still computed for diagnostics.

### The traps grounding catches

The catalog's `message` field is frequently misleading. **Always trust
`qna_description`.**

| Entry | Says | Actually means | Consequence |
|---|---|---|---|
| `DL-0022` | "View Reset Options" | auto-reset after failed unlock attempts | no factory-reset entry exists; embeds at **0.787** on a factory-reset step |
| `DL-0349` | "View Reset Options" | TalkBack verbosity level | same misleading message, unrelated feature |
| `DL-0058/59` | "Disable/Enable Charging" | vibration feedback while charging | "Charge the Device" must not match |
| `DL-0479/80` | "Disable/Enable Auto Restart" | *scheduled* auto-restart | "Force a Restart" must not match |
| `DL-0116` | "View More options" | navigation gesture controls | right meaning, useless label |

### Polarity

111 settings — 222 entries, **38% of the catalog** — exist as both "Enable X" and
"Disable X" **with an identical `qna_description`**. No embedding can separate them, so
direction is resolved from the step text. When the text gives no direction the matcher
returns **both** halves rather than guessing; that is why the return type is a list.

Get this wrong and you return the right setting with the wrong direction, which looks
correct and is not. The real SIIS touch-sensitivity text contains *both* "is enabled"
and "to turn off this feature", and the instruction is the later clause.

---

## Cold start and deployment (M2 / M3)

* **Pinned model:** `BAAI/bge-small-en-v1.5`, 384 dims, 67 MB, ONNX via `onnxruntime`.
  No torch at runtime. Changing the model invalidates the committed index and every
  threshold — don't float it.
* **Download the model at Docker build time**, not on first request. It lands in
  `backend/matcher/.model_cache` (gitignored) or wherever `BIXEL_MODEL_CACHE` points.
  fastembed's own default is the system temp directory, which a temp sweep can clear
  and turn a cold start into a 30-second download.
* **The index is committed** (`index/catalog_index.npz`, 802 KB). Building it takes
  ~45 s, which does not fit an 8-second cold-start budget. Its manifest carries a
  SHA-256 of `deeplinks.json`, and `load_index()` **raises** if the catalog has changed
  — a stale index returns plausible but wrong scores.
* Similarity is brute-force over 578 × 384 floats: tens of microseconds. No FAISS.

---

## Running it

```bash
# NOTE: `python` on this machine is Inkscape's bundled interpreter and has no pip.
PY="$LOCALAPPDATA/Programs/Python/Python312/python.exe"

"$PY" -m pytest backend/matcher/tests -q      # 102 tests
"$PY" -m backend.matcher.evaluate             # sweep + selected gate + confusion cases
"$PY" -m backend.matcher.build_index          # only after deeplinks.json changes
```

## Files

| File | Purpose |
|---|---|
| `catalog.py` | load 578 entries, derive blobs, polarity, grounding terms |
| `embedder.py` | pinned fastembed model, query prefix, cosine |
| `build_index.py` | build/load the committed index, with drift checks |
| `polarity.py` | resolve enable/disable direction from step text |
| `matcher.py` | `match_deeplinks()`, the gate, ranking, abstention |
| `siis_groups.py` | split SIIS docs into `##` groups (for evaluation only) |
| `labels.py` | 38 hand-labelled groups + 9 adversarial cases, each with a reason |
| `evaluate.py` | threshold sweep and selection |

`labels.py` carries a written justification for every label. If you disagree with one,
change it and re-run `evaluate.py` — that is the intended way to challenge this module.
