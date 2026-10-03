# Bixel Doctor + Laya — presentation brief

Branch: integrated onto `main` on 2026-10-03 (the spike work came from `feat/impact-extension` and `feat/laya-system1`; `tools/` is excluded from the deployed image). Written 2026-10-03. Every number below was measured in this project; where a number is
weak, the weakness is stated next to it. Implementation details are in `docs/laya-implementation.md`.

## 1. One sentence

**A tiny, fast decision model that reads what is actually happening on a Galaxy phone, decides which setting to change, changes
it, and proves from the operating system itself that it worked, and puts it back if you ask.**

## 2. The problem

Troubleshooting guides (Samsung's SIIS documents) tell users to "open Settings, tap Display, turn the switch off". It is
text. Users read it, get lost, or give up. Other teams on this theme also read SIIS and return grounded steps, which is
text in, text out. A judge sees a document, not a result.

## 3. The idea

1. **See** the phone: battery, temperature, CPU, memory, top apps, and the current value of each controllable setting.
2. **Decide** with a *System-1* model (Laya): one forward pass picks the right setting for a complaint like "my game is lagging",
   or abstains when the complaint is not something we can fix.
3. **Act** safely: only allowlisted, reversible settings, only from a fixed value set. The model never writes a command.
4. **Prove**: read the value back *and* check the operating system's own state, then show before/after.
5. **Undo**: restore the original and read it back again.

**Profiles** (default, user-preferred, performance, battery saver) are bundles of setting values. The model picks which
settings to move and toward which profile. The "default" profile is *captured from the phone's current state at first run*,
because Samsung's real factory values cannot be read over adb.

```
complaint + state + config + profiles ──▶ Laya (choice / score / probability)
        ──▶ deterministic validator (allowlist, ranges, risk tier) ──▶ adb executor
        ──▶ OS-verified before/after ──▶ restore
```

## 4. What is built and proven today

Proof of concept on a real **Galaxy S24 (SM-S921B), Android 16, One UI 8.5**, over adb.
Code: `tools/adb_spike/`, `tools/bixel_doctor/`, `tools/laya_spike/`. The graded app (`app/`, `backend/`, `/v1/troubleshoot`) is untouched.

### 4.1 Spike: can we read, change, verify and restore real settings?

| Setting | Real key | Read | Write | Restore confirmed | OS effect proven | Catalog entry (our matcher) |
|---|---|---|---|---|---|---|
| Adaptive brightness | `system.screen_brightness_mode` | yes | yes | yes | **yes** (`mUseAutoBrightness` true→false) | DL-0020 / DL-0021 |
| Screen timeout | `system.screen_off_timeout` | yes | yes | yes | **yes** (`mScreenOffTimeoutSetting` 120000→60000) | DL-0220 |
| Dark mode | `cmd uimode night yes\|no` | yes | yes (via `cmd` only) | yes | **yes** (`yes`→`no`) | DL-0078 |
| Motion smoothness | `secure.refresh_rate_mode` (1 Adaptive, 0 Standard) | yes | yes | yes | **no** (stored value only; probe inconclusive) | DL-0228 |
| Touch sensitivity | `system.auto_adjust_touch` | yes | yes | yes | **no** (stored value only) | DL-0125 / DL-0126 |

**Key finding:** writing the dark-mode settings key (`secure.ui_night_mode`) changes the number but **Android ignores it**; only
`cmd uimode night` changes the real state. A value read-back alone is not proof, which is why the Doctor checks OS state.
Motion smoothness and touch sensitivity are therefore **read-only diagnostics**: we showed the stored value moves, not that
the phone's behaviour does, and we will not demo what we cannot back up.

### 4.2 Bixel Doctor: complaint → verified change on the phone

Three authored SIIS-style documents (labelled as ours), each with distractor steps that must trigger nothing.

| Complaint | Step picked | Catalog entry | OS state before → after | Restored |
|---|---|---|---|---|
| "Brightness keeps changing by itself…" | Turn Off Adaptive Brightness | DL-0020 | `true` → `false` | yes |
| "Screen turns itself off after a few seconds…" | Extend the Screen Timeout (5 minutes) | DL-0220 | `120000` → `300000` | yes |
| "Everything turned black, can't read in sunlight" | Turn Off Dark Mode | DL-0078 | `yes` → `no` | yes |
| "My phone speaker makes no sound during calls" | none | none | untouched | n/a |

- Catalog entry is chosen by **`validation.key` and polarity, never by top matcher score**. The matcher really does
  return "Extra brightness" (0.811) and "Adaptive color tone" (0.790) right behind "Adaptive brightness" (0.831); a test
  forces "Extra brightness" to outscore the right entry and still gets DL-0020.
- Default mode **reverts after verification**; keeping a change needs an explicit `--keep`.
- Tests: **41 offline pass, 3 live (real phone) pass**; the pre-existing repo suite (**488 passed, 28 skipped**) is unchanged. Whole repo: **529 passed, 31 skipped**.
- A pre-flight script checks the phone is connected and authorised, and runs a write/effect/restore cycle on all three
  controls. **It cannot read Auto Blocker** (no settings key exposes it); that item is a manual eyeball check.

### 4.3 Laya (System-1 decision model)

Laya (Convai Innovations, Apache-2.0, `pip install laya`, v0.3.24) returns typed answers (choice / score / probability) in a
single forward pass, with calibrated confidence and an abstention option. Our description of it comes from the project's
public documentation and the package metadata; latency below was measured on our own laptop.

**Comparison on 38 labelled complaints** (24 real-setting complaints, 14 off-topic), same complaints for both systems:

| | Accuracy | Acted on an off-topic complaint | Refused a real complaint | Wrong setting | Median latency (CPU) |
|---|---|---|---|---|---|
| Current selector (embedding similarity + floor) | 0.737 | 1 of 14 | 8 of 24 | 1 | ≈ 20 ms |
| **Laya** | **0.895** | **0 of 14** | 4 of 24 | **0** | ≈ 346 ms |

- **Laya's failures were all safe:** its four misses were refusals ("none"), never a wrong action.
- **The selector's one false action** was applying *screen timeout* to "my phone is very slow and games lag" at 0.66, right on its floor.
- **Confidence does not separate right from wrong on this set.** Laya's 34 correct answers had `answer_confidence` 0.37–0.99
  (median 0.71; real-setting complaints median 0.82, off-topic refusals 0.52–0.87), while its 4 wrong answers sat at
  0.40–0.63. The ranges overlap, so a single abstention threshold would either let some errors through or reject some
  correct answers. Whether calibration on a proper dataset fixes this is an open question, not a result.

**Why this must not be oversold**
- One author wrote the complaints, the test documents and Laya's label descriptions. The selector got no label descriptions,
  and its floor was tuned on some of these same complaints.
- 38 rows: 34 vs 28 correct is a modest gap, not a significant one.
- Laya is ≈ 17× slower than the selector on CPU; memory not measured; first call ≈ 3 s.
- **Both latencies must come from one machine.** Re-running `baseline` on a second laptop gave a selector median of
  48.9 ms, not 20 ms, which would make the ratio ≈ 7× rather than 17×. Re-measure both on the machine that
  runs the demo and quote that pair. (The accuracy columns reproduced exactly: 0.737, 1 of 14, 8 of 24, 1 wrong class.)
- The planned fix is an independent held-out set (≥ 150 complaints, ≥ 2 authors) in `laya-implementation.md` §8.

## 5. Proven versus proposed

| Claim | Status |
|---|---|
| A real phone's settings can be read, changed and restored over adb | **Proven** (5 settings) |
| The OS actually changed, not just a stored value | **Proven for 3** (brightness, timeout, dark mode); **not proven for 2** |
| A complaint can be routed to the right verified action | **Proven for 3 scripted complaints**; the Laya comparison is indicative only |
| A System-1 model abstains safely on off-topic complaints | **Indicated** (0 of 14 false actions on 38 rows). Its confidence score does not cleanly separate right from wrong answers, so a threshold is unproven |
| Profiles (default / preferred / performance / battery saver) | **Designed, not built** |
| State/config files updating live | **Designed, not built** (the probe exists) |
| Interactive query screen | **Designed, not built** |
| Better battery life or performance from tuning | **Not measured.** No claim is made |
| Distilled Laya running inside the phone | **Proposal only** |
| Automatic permission revoke/grant, crash troubleshooting | **Proposal only**, no testing done |

## 6. Roadmap

**Now (this branch, ≈ 3–5 days):** reconnect and pre-flight; discover and test **12 more adb-accessible settings** (reduce
animations, Always On Display, Power saving, Adaptive power saving, Do not disturb, Wi-Fi, Bluetooth, Eye comfort shield,
Extra dim, Color inversion, Touch and hold delay, Sound mode), each through the same read → write → read-back → OS-check →
restore protocol; build the state/config/profile files and the Laya wrapper; add the validator and the one-page query UI; run the
independent evaluation. Details, risks and acceptance criteria: `docs/laya-implementation.md`.

**Next (after the hackathon):** measure real performance effects (frame-time and jank statistics before/after a profile);
fit abstention thresholds on a proper dataset; evaluate the ONNX/INT8 export for memory and latency; add more settings
by the same protocol; collect real before/after outcomes as training signal.

**Later (needs Samsung):** distil/retrain Laya for Samsung devices so it runs natively in the background at low resource cost;
integrate it at the system level so it can read fine-grained state that adb cannot (CPU clocks, RAM pressure, per-app wakelocks)
and change attributes only the OS can; extend to **crash troubleshooting** (from logs and state) and **automatic permission
management** (revoke unused permissions, restore on launch) under proper safety review. None of this can be done by a
normal app, which cannot run `dumpsys` or change these settings without system-level permissions.

## 7. Honest limitations

- adb is a **proof-of-concept transport**, not the product; a shipped version cannot depend on a USB cable.
- The catalog has **no plain "dark mode on/off" entry**: DL-0078's description is "dim wallpaper in Dark mode", while its
  `qna_description` says it switches to a dark theme. The Doctor maps to it and then runs `cmd uimode night`. We flag this
  rather than hide it.
- Dark mode clears the matcher gate by only 0.009 (0.779 vs 0.77); a regression test fails if it falls by 0.005.
- Document selection has a thin margin (right documents 0.69–0.82, wrong ones up to ≈ 0.63) and was tuned on our own set.
  Use scripted complaints in a live demo.
- Catalog `message` text can be misleading: while preparing this brief we found that Wi-Fi `DL-0309/0310` and Bluetooth
  `DL-0042/0043` are the *scanning* settings, not the toggles (real toggles: DL-0573/0574 and DL-0494/0495). We now select by
  `validation.key` and `qna_description` only.
- Auto Blocker cannot be read over adb; USB-debugging authorisation expires after 7 days without a connection.

## 8. Demo flow (≈ 3 minutes, scripted)

1. `preflight` → READY (phone connected, authorised, three controls cycle cleanly).
2. Type a complaint into the query box. Show the state panel (battery, temperature, current settings).
3. Laya's decision with its confidence; the matched catalog entry (by `validation.key`).
4. The phone changes on camera; the result shows **OS-state before → after**.
5. Type an off-topic complaint: the system **refuses** and touches nothing.
6. Press Undo: the original value is restored and read back.

## 9. Questions a judge may ask

- **"Did that actually do anything?"** For brightness, timeout and dark mode, yes: we read the operating system's own state, not
  only the stored value. For motion smoothness and touch sensitivity we do not claim it, so we do not act on them.
- **"Is this just a classifier?"** Yes, deliberately: a System-1 model can only choose from options we allow. It cannot invent a command.
- **"How did you measure Laya?"** 38 self-written complaints; indicative only. An independent held-out evaluation is the next step.
- **"Does it run on the phone?"** Not today. Everything runs on a laptop over adb. On-device distillation is a proposal that needs Samsung's support.
- **"Why not just use an LLM?"** Latency, calibrated abstention, and the guarantee that the model's output space is a fixed set of verified actions.
- **"Is it safe?"** Allowlist only, reversible settings only, revert by default, destructive commands blocked in code, protected app code untouched.

## 10. Reproduce

Run every command from the repo root. `adb` is found via the `ADB` environment variable, then
`PATH`, then `~/platform-tools/adb` (`adb.exe` on Windows).

```
# read-only probe of the phone
python -m tools.adb_spike.probe
# pre-flight and the Doctor (revert by default)
python -m tools.bixel_doctor.preflight
python -m tools.bixel_doctor.doctor "My screen turns itself off after a few seconds while I am reading."
# offline tests (no phone); live tests drive the phone and revert
python -m pytest tools -q
BIXEL_LIVE=1 python -m pytest tools/bixel_doctor/tests/test_doctor_live.py -q
# Laya vs selector comparison, in order: baseline and laya_run write out/, compare reads it.
# Laya needs its own venv with torch -- see laya-implementation.md section 3.3.
python -m tools.laya_spike.baseline
<laya-venv>/python tools/laya_spike/laya_run.py
python -m tools.laya_spike.compare
```
