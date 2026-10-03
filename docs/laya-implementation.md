# Laya System-1 tuner — implementation plan

Branch: `feat/laya-system1` (cut from `feat/impact-extension`). Written 2026-10-03.
Status: **plan only.** Nothing in this document beyond the "What exists today" section has been built or verified.

## 1. Goal and non-goals

**Goal.** Show, on a real Galaxy S24 over adb, that a fast System-1 decision model (Laya) can turn a plain-language
complaint plus live device state into a *safe, verified* settings change, and report before/after proof from the OS
itself. adb is the proof-of-concept transport. The product idea (see `Presentation.md`) is a distilled model running
inside the device; that is roadmap, not part of this build.

**Non-goals (explicitly out of scope for this build):**
- Running anything on the phone itself. Everything runs on the laptop over adb.
- Training, fine-tuning or distilling a model. We use the published checkpoint as-is.
- Revoking or granting app permissions, clearing app data, force-stopping apps, anything destructive.
- Touching `main`, the `v1.0.0` tag, `app/`, `backend/`, the graded `/v1/troubleshoot` route, the response schema or
  the Render deployment. All code lives under `tools/`.
- Claims of better battery life or performance, unless Phase 5's measurement actually shows it.

## 2. What exists today (verified, on `feat/impact-extension`)

| Piece | Where | State |
|---|---|---|
| adb wrapper with a destructive-command guard | `tools/adb_spike/adb.py` | working |
| Read-only device probe (battery, display, top apps) | `tools/adb_spike/probe.py` | working |
| Key discovery by snapshot diff | `tools/adb_spike/discover_setting.py` | working; needs a human to flip one toggle |
| Read → write → read-back → restore → read-back cycle | `tools/adb_spike/toggle_test.py` | 5 settings, 3 effect-verified |
| Orchestrator: complaint → matcher → catalog entry → adb → OS-verified result | `tools/bixel_doctor/` | 3 flagship controls; reverts by default |
| Pre-flight (connected, authorised, write cycle) | `tools/bixel_doctor/preflight.py` | working; Auto Blocker can't be read over adb |
| Laya vs current selector comparison | `tools/laya_spike/` | indicative result only (38 self-written complaints) |

Verified controls today: **adaptive brightness** (`system.screen_brightness_mode`, OS probe `mUseAutoBrightness`),
**screen timeout** (`system.screen_off_timeout`, OS probe `mScreenOffTimeoutSetting`), **dark mode** (only via
`cmd uimode night yes|no`; writing `secure.ui_night_mode` is ignored by Android).
Stored-value-only, therefore *not* acted on: motion smoothness (`secure.refresh_rate_mode`), touch sensitivity
(`system.auto_adjust_touch`).

## 3. Architecture

```
 complaint ─┐
            ▼
   ┌──────────────────┐     state.json   ┌──────────────┐
   │ Collector (adb)  │────────────────▶│              │
   │ battery, temp,   │     config.json  │  Laya Router │  typed answers:
   │ cpu, ram, top    │────────────────▶│  (System-1)  │  choice / score / noul
   │ apps, settings   │   profiles/*.json│              │  + calibrated confidence
   └──────────────────┘────────────────▶└──────┬───────┘
                                               ▼
                                   ┌────────────────────────┐
                                   │ Proposal validator     │  allowlist + ranges + risk tier,
                                   │ (deterministic)        │  profile-conflict rules, abstain
                                   └──────────┬─────────────┘
                                              ▼
                                   ┌────────────────────────┐
                                   │ Executor (existing     │  read → apply → read back →
                                   │ Doctor controls)       │  OS-state check → restore
                                   └──────────┬─────────────┘
                                              ▼
                              result.json: before / after / verdict / evidence
```

**Rule that never bends:** the model *chooses*; it never emits a command or a free-form value. Every action is a
registry entry (§4) with a fixed allowed value set. Anything outside the registry, or below the confidence gate, is
a refusal and nothing on the phone is touched.

### 3.1 Files and contracts

`state.json` — refreshed on demand (and optionally on a timer), written atomically, **never committed**:
```json
{ "t": "2026-10-03T10:00:00Z",
  "battery": {"level_percent": 35, "temperature_celsius": 30.0, "charging": "charging"},
  "cpu": {"top": [{"process": "…", "cpu_percent": 2.7}]},
  "memory": {"available_mb": 0, "source": "dumpsys meminfo"},
  "top_battery_apps": [{"app": "…", "mah_since_charge": 0}],
  "wakefulness": "Awake" }
```
`config.json` — current value of every registry setting: `{id: {"stored": …, "os": …}}`, read live, same atomic write.

`profiles/*.json` — each profile is a map `{setting_id: value}` over registry ids only:

| Profile | Meaning | Honest caveat |
|---|---|---|
| `default` | **Captured, not factory.** First run snapshots the phone's current values for every registry setting. | We cannot know Samsung's factory values over adb; the profile is "what this phone was when you started". |
| `user_preferred` | User-set values for the registry settings. | Needs a small editor in the UI. |
| `performance` | e.g. animations reduced, Power saving off, Adaptive brightness per user. | Which values are "performance" must come from measured results (Phase 5), not assumption. |
| `battery_saver` | e.g. Power saving on, Always On Display off, Extra dim per user. | Same. Power saving moves many keys at once (§6, setting 3). |

"Mixing profiles" means: Laya selects *which settings* to change and *toward which profile's value*; the validator
applies a fixed precedence (user pin > safety tier > profile value) and rejects mixes that touch a setting twice.

### 3.2 Laya questions

Laya returns typed answers per question (verified API: `Router().predict(state, questions)`; `choice` questions take
`criteria` as `label → description`). Planned questions over `{complaint, device state summary}`:

1. `setting` — `choice` over registry ids plus `none`. Primary decision.
2. `direction` — `choice` over `on | off`, only asked for directional settings, and only when the text doesn't decide it
   deterministically (the existing polarity resolver goes first; Laya is the second opinion).
3. `urgency` — `score`, optional, used only to order multiple proposals.
4. `performance_related` — `noul` (P(true)), used to decide whether to look at CPU/RAM/state at all.

**Abstention:** use Laya's `min_confidence` (calibrated `answer_confidence`), with a threshold fitted on the evaluation set
in §8, and fail closed. **Caution from the spike:** on 38 rows, correct answers had confidence 0.37–0.99 (median 0.71) and
the 4 wrong answers 0.40–0.63, so the ranges overlap and one threshold does not cleanly separate them. The primary safety
mechanism is therefore the deterministic validator plus the selector agreement rule below, with confidence as an extra
filter. Phase 5 must report the precision/recall trade-off of the threshold on the held-out set rather than assume it works.

**Ensemble with the existing selector** (embedding matcher, kept as fallback): act only if Laya and the selector agree,
*or* Laya's confidence clears the gate and the selector abstains. A disagreement on a non-`none` class is a refusal.
Whether the ensemble beats Laya alone is something the evaluation decides, not an assumption.

### 3.3 Packaging constraints (found during the spike)

- `laya` 0.3.24 (Apache-2.0) pulls in `torch` and downloads Hugging Face checkpoints on first use (≈ minutes).
  It runs in **its own venv**, never in the repo's pinned `.venv` (the matcher's pins are fragile, see
  `requirements.txt`). The tool talks to it through a small local subprocess/HTTP wrapper.
- Measured on this laptop's CPU: median ≈ 346 ms per complaint after warm-up (selector: ≈ 20 ms). First call ≈ 3 s.
  Memory not measured. There is an ONNX / INT8 export path in the package; evaluating it is a Phase 2 task.
- Pin `laya==0.3.24` and record the checkpoint revision in the results, as the matcher does.

## 4. Setting registry

One entry per controllable setting; the executor reads only this. Fields:

```
id, label, catalog_ids {on, off | value}, validation_key,
read_stored(), read_os(), apply(value) -> command string, restore(original),
expected_os(value), allowed_values, risk_tier (1 safe / 2 reversible-with-side-effects / 3 held back),
side_effect_keys[]  # keys that must be snapshotted and restored together
```
Registry additions require: (a) a catalog entry chosen by **`validation.key` and `qna_description`, never `message`**
(Wi-Fi `DL-0309/0310` turned out to be "Wi-Fi *scanning*"; the real toggle is `DL-0573/0574`), (b) a passing §6 cycle,
(c) an OS-effect probe, (d) a regression test pinning the matcher score for its step text.

## 5. Phases

Each phase ends with a commit on `feat/laya-system1` and a short result note. Estimates assume the phone stays
connected; phone-time is the scarce resource because every new key needs a manual flip by the user.

| Phase | Work | Output | Acceptance | Est. |
|---|---|---|---|---|
| 0 | Reconnect phone, run `preflight.py`, re-confirm 3 flagship controls | green pre-flight | all PASS; Auto Blocker eyeballed | 0.25 d |
| 1 | **Discover and verify ≥ 10 more settings** (§6) with the registry schema | `registry.py`, `FINDINGS_2.md` | ≥ 10 settings with a recorded key, read/write/restore cycle, OS probe or an honest "stored-only" flag | 1.5 d |
| 2 | Collector + `state.json` / `config.json` + profile files; Laya wrapper (own venv); ONNX/INT8 and memory check | state/config/profiles, `laya_client.py` | state refresh < 5 s; Laya answers < 1 s warm; memory number recorded | 1 d |
| 3 | Proposal validator + executor wiring + ensemble rule + abstention gate | `tuner.py` | every refusal path tested offline; live cycle restores | 1 d |
| 4 | Interactive screen: local FastAPI + one HTML page, **separate from `app/`**; query box, proposal, evidence, before/after, undo button | `tools/laya_tuner/ui/` | scripted complaint round-trips on the phone | 0.5 d |
| 5 | Evaluation (§8), optional frame-stats measurement for performance claims, write results into `Presentation.md` | results tables | numbers reproduced by one command | 0.75 d |

Total ≈ 5 days of work for one person; **the "3–4 day" estimate holds only if Phase 1 is capped at 10 settings and
Phase 4 stays a single page.** Cut order if time runs out: Phase 5 frame-stats, then the `urgency` question, then the
ensemble (use Laya alone), never the verification step.

## 6. Testing ≥ 10 more settings over adb

> **Status 2026-10-04: executed.** 13 settings cycled, 7 with an OS-observed effect, 5 stored-only, 1 unknown; plus 3 discovered
> by flip and not actionable. Results, side effects and the tier recommendation are in `tools/adb_spike/FINDINGS_2.md`. Not yet done: matcher
> mapping (protocol step 5) and regression tests for the new entries. Notable deviations from this section's hypotheses: Wi-Fi/Bluetooth/Mobile data
> needed `cmd`/`svc` paths, DND needed `cmd notification set_dnd`, Power saving cannot be reproduced by adb, and Bluetooth has real side effects.

All candidates have a real catalog Enable/Disable (or value) entry, are reversible, and expose some OS state worth
probing. **Every key and probe below is a hypothesis until Phase 1 confirms it.** Catalog ids were checked against
`validation.key` and `qna_description` on 2026-10-03.

| # | Setting | Catalog ids | Probable key / command | OS-effect probe to try | Tier | Notes / side effects |
|---|---|---|---|---|---|---|
| 1 | Reduce animations | DL-0285/0286 | `global.window_animation_scale`, `transition_animation_scale`, `animator_duration_scale` | `dumpsys window` animation scales | 1 | Three keys restored together. Strongest "feels faster" demo. |
| 2 | Always On Display | DL-0482/0483 | `system.aod_mode` (many `aod_*` keys exist) | `dumpsys power` / display doze state | 1 | Several `aod_*` keys change just because the screen wakes; ignore in diffs. |
| 3 | Power saving | DL-0411/0412 | `global.low_power`, Samsung `psm_*` / `sem_power_mode_*` | `dumpsys power` low-power flag | **2** | Moves many other keys (refresh rate, brightness cap). Snapshot all of `settings list` before and after; restore every changed key. Test last. |
| 4 | Adaptive power saving | DL-0399/0400 | Samsung `psm_*` / adaptive keys (to discover) | OS flag to discover | 2 | Overlaps #3; test after it so diffs are attributable. |
| 5 | Do not disturb | DL-0505/0506 | `global.zen_mode` or `cmd notification set_dnd` | `zen_mode` and notification-service state | 1 | May silence real calls during the test; restore promptly. |
| 6 | Wi-Fi | DL-0573/0574 | `cmd wifi set-wifi-enabled enabled\|disabled` | `cmd wifi status` | 2 | Drops the network until restored. adb is over USB, so unaffected. Do not run while anything depends on Wi-Fi. |
| 7 | Bluetooth | DL-0494/0495 | `cmd bluetooth_manager enable\|disable` or `global.bluetooth_on` | `dumpsys bluetooth_manager` | 2 | Disconnects earbuds/watch; run with nothing connected. |
| 8 | Eye comfort shield | DL-0039/0040 | blue-light-filter keys (to discover) | display colour/filter state | 1 | Cosmetic but visible on camera. |
| 9 | Extra dim | DL-0203/0204 | `secure` reduce-bright-colors keys (to discover) | display / accessibility state | 1 | Interacts with brightness; record both. |
| 10 | Color inversion | DL-0280/0281 | `secure.accessibility_display_inversion_enabled` | `settings` + accessibility service state | 1 | Very visible; make sure it is restored, the screen is hard to use inverted. |
| 11 | Touch and hold delay | DL-0234 (value) | `secure.long_press_timeout` (seen: 500) | accessibility/input config | 1 | Value setting: allowed set must be Samsung's menu choices. |
| 12 | Sound mode | DL-0260 | `cmd audio` / `settings system` ringer keys (to discover) | audio service ringer mode | 2 | Vibrate/mute change; restore promptly. |

**Held back on purpose:** Airplane mode, Mobile data, Location, NFC (can cut connectivity or touch privacy-sensitive
state); app permissions (different safety rules, untested); screen resolution; anything needing root or a signature permission.

### 6.1 Per-setting protocol (identical for all 12)

1. **Discover** the key if unknown: `discover_setting.py before <label>` → user flips exactly one toggle by hand
   (the assistant tells the user precisely where) → `after` → `diff`. Keys that change on their own are filtered by a
   two-snapshot noise check; `settings_change_history` is always ignored.
2. **Cycle:** read original → write → read back → restore → read back (restore in a `finally`).
3. **OS-effect check:** a runtime probe must change when the write happens. If only the stored value moves, the
   setting is marked `stored-only` and is **not** added to the acted-on registry (the dark-mode lesson).
4. **Side-effect snapshot** for tier 2: diff the full `settings list` (three namespaces) before and after; every key
   that moved is recorded and restored.
5. **Catalog mapping** through our own matcher (`match_deeplinks`) on SIIS-style step text; record id, score, polarity.
   Add a regression test that fails if the score drops.
6. **Record** key, readable/writable/restore/effect, catalog id, matcher score, risks in `FINDINGS_2.md`.

**Stop rules.** Phone not listed by `adb devices`, state `unauthorized`, any restore not confirmed, or an unexpected
popup → stop, restore what can be restored, and tell the user exactly what is on screen. Never guess.

**Pass criterion for "tested":** steps 2, 3 (or an honest `stored-only` flag) and 5 done. The target is **≥ 10 settings
tested**; it is not "≥ 10 settings acted on". Expect some to end up `stored-only`.

## 7. Safety rules carried forward

- Allowlist only; no model-generated commands; the adb wrapper's destructive-command guard stays.
- Default is **revert after verification**; keeping a change is an explicit opt-in (`--keep`).
- Device serial, package lists and raw dumps never leave the laptop (gitignored `out/`, `dumps/`).
- Pre-flight before any demo; Auto Blocker is MANUAL (not readable over adb).
- Push to `feat/laya-system1` only; no PR, no merge, no redeploy.

## 8. Evaluation plan

The spike's numbers (0.895 vs 0.737) came from 38 complaints written by one author who also wrote the label
descriptions. That is a smoke test. For anything shown to judges:

- **Set:** ≥ 150 complaints across the registry settings plus ≥ 40 off-topic and near-miss negatives, written by **at
  least two people who have not seen the label descriptions**, including paraphrases and a few non-English lines.
  Held out: never used to tune a threshold or a prompt.
- **Systems compared:** current selector, Laya alone, Laya + selector ensemble.
- **Metrics:** accuracy; **acted-on-off-topic count (hard gate: 0)**; refused-real rate; wrong-class count; latency
  median and p95; memory.
- **Calibration:** fit the abstention threshold on a separate dev split; report on the held-out split only.
- **Live check:** run the top-N scripted complaints end to end on the phone with revert, assert OS-verified change and
  confirmed restore (same shape as `test_doctor_live.py`, opt-in via `BIXEL_LIVE=1`).
- **Performance claims:** only if Phase 5 measures it. Candidate: `dumpsys gfxinfo` jank percentage and frame-time
  percentiles during a scripted scroll, before/after a profile. If that measurement is noisy or unavailable, the
  presentation says "state-aware routing", not "faster".

## 9. Risks and mitigations

| Risk | Why it matters | Mitigation |
|---|---|---|
| Fewer settings turn out effect-verifiable than 10 | The demo rests on verified actions | Count tested vs acted-on separately; keep `stored-only` ones as read-only diagnostics |
| Laya's advantage is an artefact of our test set | Judge asks "how did you measure?" | §8 independent held-out set; report the ensemble honestly |
| Power saving / other tier-2 settings cascade | One toggle moves many keys, restore may be incomplete | Full-namespace snapshot diff and restore; test last; abort on any unrestored key |
| Catalog entry mismatches the action (dark mode `DL-0078` is "dim wallpaper in Dark mode" by description) | Looks wrong to a careful judge | State the mismatch openly in the deck; decide keep/cut with the team |
| Auto Blocker / USB authorisation lapses mid-demo | Demo dies | `preflight.py` before filming; 7-day authorisation window noted |
| `torch` + checkpoints are heavy | Setup time, disk | Separate venv, pinned version, ONNX/INT8 evaluation, pre-download before the demo |
| Source of Laya facts is community documentation | Claims about size/latency may be off | Re-measure locally; cite measured numbers only |
| Scope creep toward on-device distillation | No data, no time | Keep as roadmap; do not start |

## 10. Decisions needed from the team

1. Keep or cut **dark mode** given the catalog description mismatch?
2. Laya alone, or Laya + selector ensemble, as the default router (decide after §8)?
3. Who writes the independent complaint set (need ≥ 2 people)?
4. Is the presentation allowed to describe on-device distillation as a proposal, given the hackathon's rules on what must
   be built versus proposed?
5. Should the profiles' "performance" and "battery saver" values be user-chosen, or derived from Phase 5 measurements?

## 11. Definition of done

- ≥ 10 additional settings **tested** and documented in `FINDINGS_2.md`, with the acted-on subset in the registry.
- `tuner.py` takes a complaint, returns a verified before/after result for ≥ 5 distinct settings on the real phone, and
  restores by default.
- Held-out evaluation numbers in `Presentation.md`, reproducible with one documented command.
- Pre-flight passes; all offline tests pass; the live test passes with `BIXEL_LIVE=1`.
- Protected paths unchanged versus `v1.0.0` (empty `git diff --stat`).
