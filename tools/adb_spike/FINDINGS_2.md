# adb spike, round 2: thirteen more settings

Device: Galaxy S24 (SM-S921B), Android 16, One UI 8.5, tested 2026-10-04 over USB adb.
Harness: `settings_matrix.py`. Per setting: snapshot all three settings namespaces twice (self-moving keys become the noise
filter) → read original → write → read back → OS-effect probe → restore → read back → snapshot again.
**PASS** needs: write read back, restore confirmed, and the *whole* namespace identical to the start. Final single-pass results
are in the table; the phone was verified back at its starting values on 15 keys at the end of the session.

Effect column: **observed** = an OS-visible state changed (a `dumpsys` field or a command's own status), **stored-only** = the value
changed but we found no OS-visible state to prove Android acted on it, **NOT-OBSERVED** = the probe did not move.

## Results

| Setting | Catalog ids* | Working write path | Effect | Verdict | Notes |
|---|---|---|---|---|---|
| Do not disturb | DL-0505/0506 | `cmd notification set_dnd priority\|off` | **observed** (`mZenMode` OFF→IMPORTANT_INTERRUPTIONS) | PASS | `global.zen_mode_config_etag` (an OS version counter) changes on every DND change; allow-listed and reported |
| DND via settings key | same | `settings put global zen_mode 1` | not observed | XFAIL (expected) | Android reverts the write within seconds. Same trap as dark mode: use the `cmd` path |
| Extra dim | DL-0203/0204 | `secure.reduce_bright_colors_activated` | **observed** (`dumpsys color_display` "Activated" true→false) | PASS | cleanest result of the round |
| Sound mode (ringer) | DL-0260 | `cmd audio set-ringer-mode` | **observed** (`dumpsys audio` mode VIBRATE↔NORMAL) | PASS | the first run failed only because my test value equalled the current state; fixed to always pick the opposite |
| Wi-Fi | DL-0573/0574 | `cmd wifi set-wifi-enabled` | **observed** (`cmd wifi status`) | PASS | |
| Mobile data | DL-0081/0082 | `svc data enable\|disable` | **observed** (`telephony.registry` data state 2→0) | PASS | briefly cuts the phone's own internet |
| Battery Saver | DL-0411/0412 | `settings put global low_power 1` | **observed** (`dumpsys power` "Battery Saver is currently: ON") | PASS, **partial** | This is Android's plain Battery Saver (2 keys). It does **not** trigger Samsung's Power saving bundle (see below) |
| Bluetooth | DL-0494/0495 | `cmd bluetooth_manager enable\|disable` | **observed** (`dumpsys bluetooth_manager` enabled true→false) | PASS, **with a real side effect** | see "Bluetooth" below |
| Adaptive power saving | DL-0399/0400 | `global.adaptive_power_saving_setting` (1 on, 0 off; found by a manual flip) | **not observed** | NO-EFFECT / unknown | the probe I tried (battery service "policy status") read 4 then 3 in different runs regardless of the write, so it is not tied to this setting. Effect unknown, **do not act on it** |
| Reduce animations | DL-0285/0286 | three `global.*_scale` keys | stored-only | PASS (cycle only) | no OS-visible state found |
| Always On Display | DL-0482/0483 | `system.aod_mode` | stored-only | PASS (cycle only) | no OS-visible state found |
| Eye comfort shield | DL-0039/0040 | `system.blue_light_filter` | stored-only | PASS (cycle only) | no OS-visible state found |
| Color inversion | DL-0280/0281 | `secure.accessibility_display_inversion_enabled` | stored-only | PASS (cycle only) | key was **unset**; it now exists as an explicit `0`, which means the same thing |
| Touch and hold delay | DL-0234 | `secure.long_press_timeout` | stored-only | PASS (cycle only) | |

\* Catalog ids were checked against `validation.key` and `qna_description` on 2026-10-03 and listed in `docs/laya-implementation.md`.
**The matcher mapping (step 5 of the protocol) has not been run for these thirteen yet.**

**Counts.** 13 settings cycled; 7 with an OS-observed effect (DND, Extra dim, Sound mode, Wi-Fi, Mobile data, Battery Saver, Bluetooth);
5 stored-only; 1 effect unknown (Adaptive power saving). Together with round 1 (brightness, timeout, dark mode effect-verified; motion
smoothness and touch sensitivity stored-only) that is **10 effect-verified settings** on this phone.

## Discovered by manual flip, not acted on

- **Samsung Power saving** (Settings → Battery → Power saving, toggled by the user): moves **27 keys**. The core is
  `global.low_power` 0→1 plus `low_power_sticky`; the cascade includes `aod_mode` 1→0, `secure.refresh_rate_mode` 1→0 (Standard),
  `screen_off_timeout` 120000→30000, `sem_power_saving_adjust_brightness_factor` 1.0→0.9, `psm_network_power_saving`, dark-mode
  and refresh-rate "power_mode_state" tags. Turning it off in the UI restored 25 of the 27; the two that differed were OS bookkeeping
  (`aod_mode_before_psm`, a backup timestamp). **adb cannot reproduce this**: writing `low_power` gave Android's Battery Saver only. This
  is a ready-made example of a "profile" (a bundle of values), and it shows the OS itself moves `refresh_rate_mode`.
- **Mobile hotspot**: **not a settings key.** The only meaningful side effect was `global.wifi_scan_always_enabled` 1→0, restored on
  turning it off. State is visible read-only in `dumpsys tethering` (the `ap_*` interface sits in `TetheredState` while on). I did not
  enable it over adb: `cmd wifi start-softap` needs an SSID and password, which would mean inventing credentials for a live network.
- **Quick Share**: **not controllable through settings keys.** Choosing "Everyone for 10 minutes" changed no key. `global.mcf_quick_share_visibility` stayed at `1`
  (it only moved incidentally when Bluetooth toggled), so the real mode lives in the Quick Share app's private storage, out of adb's reach.
  Enabling Quick Share did turn Wi-Fi on (`global.wifi_on` 0→1).

## Problems found along the way (worth knowing)

1. **Bluetooth toggling disconnects connected devices and triggers Samsung Modes.** With earbuds connected, turning Bluetooth off and back on
   changed connection-owned keys (`buds_*`, `bt_a2dp_audio_latency`, `SOUNDALIVE_AUDIO_PATH`, `volume_music_bt_a2dp`) **and** Samsung Modes
   keys (`mode_*`, `mode_ringer`, `volume_system_*`). The radio restore is confirmed, but those keys are not ours to rewrite, and the earbuds did not
   reconnect on their own. This happened in two earlier runs; the final pass was clean only because no device was connected. The earlier runs are
   recorded here rather than the cleaner last one. **A Doctor action for Bluetooth needs a "no connected device" precondition or must be tier 3.**
2. **Writing a settings key is not the same as acting.** DND and dark mode both prove it; Adaptive power saving may be a third (unknown).
3. **Unexplained change:** after the Adaptive power saving flip, `system.aod_mode` read `0` although it was `1` at the start and my AOD test had
   restored it. I do not know the cause (candidates: the user's flips, or Samsung's power-saving logic). I restored it to `1` and read it back; I left
   other keys that differed (ringer mode, Modes names, earbud keys) alone because they track device connection and user actions.
4. **Samsung's Power saving cannot be applied by adb**, only Android's Battery Saver.
5. A first harness version labelled "stored but not applied" results as PASS; it now reports `NO-EFFECT`, and expected-negative evidence rows are `XFAIL`.

## What this means for the Doctor / Laya build

| Tier | Settings | Why |
|---|---|---|
| 1, safe to act on | Extra dim, Do not disturb (cmd), Sound mode (cmd) | effect observed, restores cleanly, no collateral keys |
| 2, act with preconditions | Wi-Fi, Mobile data (cut connectivity), Bluetooth (needs no connected device), Battery Saver (AOSP only, say so) | effect observed, real side effects |
| Read-only diagnostics | Reduce animations, AOD, Eye comfort, Inversion, Touch and hold, Adaptive power saving, Motion smoothness, Touch sensitivity | no proof Android acted |
| Out of reach over adb | Samsung Power saving bundle, Mobile hotspot, Quick Share mode | not settings keys or no safe write path |

## Re-run on 2026-10-04 under the corrected verdict logic

The harness verdict logic was fixed after this round was recorded (`XFAIL` was unreachable; notes overwrote each other).
`settings_matrix.py` was re-run end to end on the same phone to check this table still holds. **It does**, with two
corrections and one new defect found and fixed.

* **Both key findings reproduce.** `dnd_key` now reports `XFAIL` as this document always claimed (it reported
  `NO-EFFECT` before the fix). All five stored-only rows, Extra dim, Sound mode, Wi-Fi, Bluetooth and DND reproduce
  exactly. Adaptive power saving is still `NOT-OBSERVED` (probe `2 -> 2`).
* **Mobile data: the probe needed longer, not a different verdict.** At a 2.5 s settle the probe caught
  `mDataConnectionState=4` (DISCONNECTING) and scored `NOT-OBSERVED`. Re-run with a 9 s settle it reads `2 -> 0` and
  PASSes. The effect is real; the wait was too short. `Spec.settle_s` is now per-setting and mobile data uses 9 s.
  **Any radio-backed setting added later needs the same treatment.**
* **Battery Saver: effect confirmed, cycle not clean.** The probe still shows `OFF -> ON`, and `global.low_power` is
  restored and confirmed. But the whole-namespace check reports `system.aod_show_state` left at a different value, so
  the row is now `FAIL` rather than `PASS, partial`. That key is runtime bookkeeping for whether AOD is currently
  rendering, not a Settings menu item; the user-facing `system.aod_mode` was verified back at `1`. Sampled on its own
  it was stable, so this is not the self-moving noise the filter catches. **Treat Battery Saver as effect-observed but
  not cleanly reversible until that key is understood**, and do not put it in tier 1.
* **A real bug in `adb.py`, found here and fixed.** `subprocess(text=True)` decodes with the Windows locale (cp1252),
  and `dumpsys bluetooth_manager` emits bytes that are not valid cp1252. The decode threw inside subprocess's reader
  thread and `shell()` returned `None`, so the Bluetooth row would have failed with
  "expected string, got NoneType" rather than a result. Now decoded as UTF-8 with replacement; the dump is 2.8 MB
  with 210 replaced bytes and parses fine.

**Counts after the re-run.** Unchanged where it matters: 13 settings cycled, **7 with an OS-observed effect**, 5
stored-only, 1 unknown; **10 effect-verified in total** across both rounds. The single change to the record is that
Battery Saver's cycle is no longer clean.

## Safety record

Every write was restored in a `finally` and verified by whole-namespace diff, and 15 keys were re-checked at the end. Changes made *to the phone by the user's hand*
during discovery (Power saving, Adaptive power saving, Hotspot, Quick Share) were reversed by the user and checked against baselines; Quick Share's
"Everyone for 10 minutes" had to be set back by the user, since adb cannot change it. No setting deleted, no reboot, no root, no package touched. Raw dumps stayed in gitignored
folders.
