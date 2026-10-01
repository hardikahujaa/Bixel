# adb spike — findings

**Question:** can we reliably read, change, verify and restore real settings on this specific phone?
**Device:** Galaxy S24 (SM-S921B), Android 16, One UI 8.5, USB debugging on, Auto Blocker off.
**Method:** every write ran read original → write → read back → restore → read back
(`toggle_test.py`; restore sits in a `finally`). Phone state after every run was confirmed equal to the start.
Catalog mapping was produced by `map_to_catalog.py`, which calls our own `match_deeplinks()`.

| Setting | Real key (namespace.key) | Readable | Writable | Restore confirmed | Catalog entry (via our matcher) |
|---|---|---|---|---|---|
| Adaptive brightness | `system.screen_brightness_mode` (1 on, 0 off) | yes | yes | yes | **DL-0020** (disable) / **DL-0021** (enable), key "Adaptive brightness". Matcher also returns "Extra brightness" DL-0104/0105 at a similar score |
| Motion smoothness / refresh rate | `secure.refresh_rate_mode` (1 Adaptive, 0 Standard) — found by a manual toggle flip | yes | yes | yes | **DL-0228** "Motion smoothness" (single, neutral-polarity entry) |
| Screen timeout | `system.screen_off_timeout` (milliseconds) | yes | yes | yes | **DL-0220** "Screen timeout" |
| Dark mode | `secure.ui_night_mode` (2 on, 1 off) — **write has no effect**; the working path is `cmd uimode night yes\|no` | yes | **key: no effect / `cmd uimode`: yes** | yes (both paths) | **DL-0078** "Dark mode settings" (neutral polarity) |
| Touch sensitivity | `system.auto_adjust_touch` (1 on, 0 off) — found by a manual toggle flip | yes | yes | yes | **DL-0125** (disable) / **DL-0126** (enable), key "Touch sensitivity" — matched from the real SIIS section |

`toggle_test.py` result (exit 0): 5 of 5 settings pass, counting dark mode via `cmd uimode`. The plain `settings put` row for dark mode is reported `XFAIL` (expected failure, kept as evidence).

**Honest split of "verified":** 5 settings are readable, writable and restorable at the key level. **3 are also effect-verified**
against runtime OS state: adaptive brightness (`dumpsys display` `mUseAutoBrightness` true→false), screen timeout
(`dumpsys power` `mScreenOffTimeoutSetting` 120000→60000) and dark mode (`cmd uimode night`, via the `cmd` path only).
**2 are stored-value only**: motion smoothness and touch sensitivity.

## What was verified, and what was not

- "Writable" means the key read back the new value. Effect was additionally proven for adaptive brightness, screen timeout
  and dark mode (above). For motion smoothness and touch sensitivity I did **not** prove the phone then behaved
  differently (display actually switching refresh rate, touch gain actually changing). I tried `dumpsys display` for
  refresh rate; the fields I read were static panel config and identical in both modes, so that was inconclusive.
- Only touch sensitivity has a real SIIS section in the 20 sample documents. For the other four the step text fed to the
  matcher is SIIS-style wording I wrote, labelled `synthetic` in the script. Their catalog mapping is real; "the SIIS
  documents would surface them" is not shown here.
- The held-out SIIS file on `main` (`testdata/unseen_siis.json`) mentions adaptive brightness once and screen timeout
  once, and none of the other three, so the SIIS step → matcher → adb chain is demonstrated end-to-end for only
  touch sensitivity, with partial support for two more.
- Dark mode cleared the matcher gate at exactly 0.77, so that mapping is fragile; a small model/text change could drop it.
- The matcher is conservative by design. For adaptive brightness it also returned the unrelated "Extra brightness"
  entry, so a build must pick the entry by `validation.key` and polarity, not take the top hit.

## What could NOT be controlled this way, and why

- **Dark mode through its settings key.** Android ignores `secure.ui_night_mode` writes; the UI-mode service owns the
  state. Only `cmd uimode night` works. Samsung's other dark-mode keys (`system.display_night_theme`, scheduling keys)
  were not tested.
- **Anything outside `settings` / `cmd`.** SIIS steps such as clearing cache, safe mode, Smart Switch, factory reset,
  force restart or app updates are either destructive (forbidden for this project) or have no catalog entry at all
  (the matcher README says so). The Doctor cannot "fix" those; it can only report them.
- **Settings not tested.** The catalog has 578 entries; I tested 5. Nothing here predicts the other 573.
- **Probe limits.** `refresh_rate_setting` is the stored mode, not the live Hz. Top battery apps come from
  `dumpsys batterystats` (mAh since last charge, per UID); secondary-user/work-profile UIDs do not
  resolve to package names and are labelled as such. Per-app numbers are estimates.
- **No permission prompt was needed** for any read or write here, but Auto Blocker (off now) can block USB commands, and
  Android can expire USB-debugging authorisation. See the risk in the go/no-go.

## Safety record

No setting was deleted, no app touched, no reboot, no root. Raw `settings list` dumps were stored only in a gitignored
folder and deleted after use. No serial, package list or dumpsys output is committed.
