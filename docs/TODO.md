# To do: everything still open

Written 2026-10-04 on `feat/laya-system1`. Companion to `laya-implementation.md` (the plan) and `Presentation.md` (the pitch).
Owner tags: **[me]** = the assistant can do it, **[you]** = needs a person (a hand on the phone, a decision, or a teammate), **[team]** = needs more than one person.
Items are grouped by what blocks what. Nothing here is done unless ticked.

## 0. Do first (small, and some change what we claim)

- [ ] **[you]** Set **Quick Share back to "Contacts only"**. It was left on "Everyone for 10 minutes" during discovery and adb cannot change it.
- [ ] **[you]** Check the phone by eye after the round-2 testing: earbuds reconnected, ringer mode and volumes as you want them (Bluetooth testing triggered Samsung Modes changes).
- [ ] **[you]** Confirm **Auto Blocker is OFF** (Settings → Security and privacy → Auto Blocker). The pre-flight cannot read it.
- [ ] **[me]** Re-run the **live** Doctor tests after the review refactor: `BIXEL_LIVE=1 pytest tools/bixel_doctor/tests/test_doctor_live.py`. The refactor of apply/restore was only covered with fakes plus the pre-flight cycle, not the live pytest run.
- [ ] **[me]** Find out why `system.aod_mode` read `0` after the Adaptive power saving flip (it was `1` at the start and had been restored by my AOD test). Unexplained; restored to `1` by hand.

## 1. Branches, `main` and deployment

- [ ] **[me]** Review Hardik's two commits on `main` (`d407f90` "Integrate adb/laya spike branches onto main's Doctor", `b840131` "Fix eight defects found auditing…") for correctness.
- [ ] **[me]** Merge `main` into `feat/laya-system1` (currently 1 ahead, 5 behind) so the Doctor on this branch picks up those fixes and my review fixes are not duplicated.
- [ ] **[you]** Check the Render deploy after the recent pushes to `main` is green and `/health` returns ok (no `autoDeploy` is pinned in `render.yaml`, so Render's default applies; I could not see the dashboard).
- [ ] **[team]** Decide whether the Laya spike and docs belong on `main`. They are already there (merged by Hardik), although I had deliberately merged only the pre-Laya work.
- [ ] **[you]** Decide what to do with `feat/impact-extension`, which still carries the pre-review Doctor and the mislabelled Wi-Fi/Bluetooth rows in `NEXT_SETTINGS.md` (revert or leave).

## 2. Finish the settings work (plan Phase 1)

- [ ] **[me]** Run the **matcher mapping** (`match_deeplinks`) for the 13 round-2 settings and record id, score and polarity. Protocol step 5 has not been done for them.
- [ ] **[me]** Add a **matcher-score regression test** per accepted setting, as for dark mode (which clears the gate by only 0.009).
- [ ] **[me]** Author SIIS-style test documents (labelled as authored) for the new tier-1/2 settings, with distractor sections.
- [ ] **[me]** Build the **setting registry** (`registry.py`) from the 10 effect-verified settings. Fields in `laya-implementation.md` §4; tier-1 first: Extra dim, Do not disturb (`cmd`), Sound mode (`cmd`).
- [ ] **[me]** Give **Bluetooth** a "no connected device" precondition, or move it to tier 3. It disconnects earbuds and fires Samsung Modes.
- [ ] **[me]** Decide and document how Wi-Fi and Mobile data are handled (they cut the phone's own connectivity).
- [ ] **[me]** Find a real **OS-effect probe** for the stored-only settings, or keep them read-only: motion smoothness, touch sensitivity, Reduce animations, Always On Display, Eye comfort shield, Color inversion, Touch and hold delay. A frame or input test may be needed (e.g. `dumpsys gfxinfo`, a scripted long-press).
- [ ] **[me]** Resolve **Adaptive power saving**: the battery-service probe I tried is not tied to it (it read 4 then 3 regardless of the write). Needs a better probe or stays read-only.
- [ ] **[me]** Investigate whether anything adb can do reproduces **Samsung's Power saving bundle** (27 keys) rather than only Android's Battery Saver. If not, model it as a profile applied key by key and say so.
- [ ] **[you]** Decide the **hotspot** question: it is not a settings key, and enabling it needs credentials. Keep as read-only (`dumpsys tethering`) or drop.
- [ ] **[you]** Hands-on discovery for any further setting added to the list (one manual flip each).
- [ ] **[me]** Untested candidates from the plan that were not run: Wi-Fi/Bluetooth *scanning* settings (`DL-0309/0310`, `DL-0041..0043`), Adaptive color tone, Airplane mode, Location, NFC (held back as risky) and app permissions (needs separate safety rules).

## 3. Build the Laya system (plan Phases 2–4)

- [ ] **[me]** **Collector**: `state.json` (battery, temperature, CPU, memory, top apps, wakefulness) and `config.json` (live value of every registry setting), atomic writes, never committed. Memory source not yet chosen.
- [ ] **[me]** **Profiles** (`default`, `user_preferred`, `performance`, `battery_saver`) as maps over registry ids. `default` is *captured at first run* (factory values are not readable over adb).
- [ ] **[you]** Decide the **performance** and **battery saver** profile values (user-chosen, or derived from measurement).
- [ ] **[me]** **Laya wrapper** in its own venv (never the repo `.venv`): pin `laya==0.3.24`, record the checkpoint revision, pre-download weights, subprocess/HTTP client.
- [ ] **[me]** Evaluate the **ONNX / INT8** export; measure Laya's **memory** (never measured) and p95 latency (only a median of ≈ 346 ms was measured).
- [ ] **[me]** **Proposal validator**: allowlist, value ranges, risk tiers, precedence (user pin > safety tier > profile), reject double-touching a setting.
- [ ] **[me]** **Executor** wiring over the registry, keeping revert-by-default and the OS-state check.
- [ ] **[me]** **Abstention rule**: Laya's confidence did **not** separate right from wrong answers on the spike set (correct 0.37–0.99, wrong 0.40–0.63). Decide the gate on a held-out set, and the **ensemble** rule (act only if Laya and the selector agree, or Laya clears the gate and the selector abstains).
- [ ] **[me]** **Interactive query screen**: local FastAPI + one HTML page, separate from `app/`; shows state, Laya's decision and confidence, the matched entry, before/after, and an Undo button.
- [ ] **[me]** Optional `urgency` (score) and `performance_related` (probability) Laya questions.

## 4. Evaluation (plan Phase 5)

- [ ] **[team]** Write an **independent complaint set**: ≥ 150 complaints across the settings plus ≥ 40 off-topic and near-miss negatives, by **at least two people** who have not seen the label descriptions, including paraphrases and a few non-English lines. The current 38-row set was written by one author.
- [ ] **[me]** Split dev / held-out; fit the abstention threshold on dev only; compare selector vs Laya vs ensemble on held-out. Hard gate: **0** off-topic complaints acted on.
- [ ] **[me]** Re-check the document selector's floor (0.66, tuned on a few complaints that are also in the test set, margin ≈ 0.03).
- [ ] **[me]** Optional performance measurement (`dumpsys gfxinfo` jank and frame-time percentiles before/after a profile). Without it the pitch says "state-aware routing", not "faster".
- [ ] **[me]** One command that reproduces every number in `Presentation.md`.

## 5. Decisions only the team can make

- [ ] **[team]** **Dark mode: keep or cut?** The catalog has no plain on/off entry (`DL-0078` is "dim wallpaper in Dark mode" by description).
- [ ] **[team]** **Laya alone, or Laya + selector ensemble**, as the default router (decide after §4).
- [ ] **[team]** Does the hackathon allow the presentation to describe **on-device distillation and permission management as proposals**, and what must be *built* versus *proposed*?
- [ ] **[team]** Is the settings-tuner direction in scope for the judging theme at all, or should it stay an experiment?
- [ ] **[you]** Whether a **keep-the-fix** mode is wanted on camera (the Doctor reverts by default).

## 6. Presentation and docs

- [ ] **[me]** Verify the Laya claims taken from community documentation (parameters, memory, license, "not official") against **PyPI, GitHub and Hugging Face**. Only latency was measured here, and only on this laptop's CPU.
- [ ] **[me]** Re-check every number in `Presentation.md` against the final result files once Phase 5 is done; update the "proven vs proposed" table.
- [ ] **[me]** Add the **demo script** with scripted complaints (off-script complaints may be refused or pick the wrong document).
- [ ] **[you]** Rehearse: pre-flight → complaint → Laya decision → phone change → off-topic refusal → Undo, on camera.
- [ ] **[me]** Roadmap slide wording for the long-term ideas (distilled on-device model, system-level integration with Samsung, crash troubleshooting, automatic permission revoke/grant). **None of these has been tested**, and the slide must say so.

## 7. Housekeeping

- [ ] **[me]** Keep raw dumps out of git (`out/`, `dumps/` are ignored); delete local `out/` results when no longer needed.
- [ ] **[me]** A pre-flight check for "no earbuds/devices connected" before Bluetooth-affecting tests.
- [ ] **[me]** Decide whether the Doctor's `tools/` stays excluded from the Docker context (`.dockerignore` currently excludes `tools`).
- [ ] **[me]** Remove the stale `NEXT_SETTINGS.md` once `FINDINGS_2.md` and the registry supersede it.

## Known weaknesses to keep stating until fixed

1. Laya's advantage is indicative only (38 self-written complaints; the same author wrote the label descriptions).
2. Laya's confidence scores overlap between right and wrong answers.
3. Only 10 settings have an observed effect; 7 are stored-only or unknown.
4. Dark mode's catalog description does not match the action taken.
5. Document selection has a thin margin and was tuned on our own set.
6. Auto Blocker cannot be read over adb; USB authorisation expires after 7 days without a connection.
7. Everything runs over adb on a laptop; no part of it runs on the phone.
