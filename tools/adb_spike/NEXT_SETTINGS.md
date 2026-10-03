# Next settings to discover over adb

Selection rules: (1) the catalog has a real Enable/Disable entry, so our matcher can map a complaint to it;
(2) reversible and harmless; (3) the OS exposes a state we can read to prove the effect (the lesson from dark mode,
where the stored key moved but Android ignored it); (4) it matters to the battery / performance / troubleshooting story.

**Nothing below is verified.** "Probable key" is a guess from names seen in `settings list`; each needs the
discovery flow (`discover_setting.py`: snapshot → you flip it by hand → diff) and then the full
read → write → read back → restore → read back cycle with an OS-effect probe. Catalog ids come from `deeplinks.json`.

| # | Setting | Catalog ids | Probable key / path (unconfirmed) | Proof of effect to try | Risk / side effects |
|---|---|---|---|---|---|
| 1 | **Power saving** (battery-saver profile) | DL-0411/0412 | `global.low_power` and Samsung `psm_*` / `sem_power_mode_*` keys (all seen in the settings dump) | `dumpsys power` low-power flag; watch the refresh-rate and brightness keys it also moves | Highest value, highest risk: it changes many other settings at once. Snapshot everything before and restore all of it, not just one key |
| 2 | **Reduce animations** | DL-0285/0286 | `global.window_animation_scale`, `transition_animation_scale`, `animator_duration_scale` (1.0 vs 0) | `dumpsys window` animation scales; the strongest "feels faster" demo | Low. Three keys to restore together |
| 3 | **Always On Display** | DL-0482/0483 | `system.aod_mode` (several `aod_*` keys exist) | `dumpsys power` / display doze state | Low. A battery lever. Beware `aod_*` keys that change just because the screen woke |
| 4 | **Wi-Fi** | DL-0573/0574 | `cmd wifi set-wifi-enabled enabled\|disabled` | `cmd wifi status` | Medium. Drops the connection until restored. adb over USB is unaffected. Troubleshooting story: "toggle Wi-Fi" |
| 5 | **Bluetooth** | DL-0494/0495 | `cmd bluetooth_manager enable\|disable`, or `global.bluetooth_on` | `dumpsys bluetooth_manager` state | Medium. Disconnects earbuds and watch. Test with nothing paired and in use |
| 6 | **Do not disturb** | DL-0505/0506 | `global.zen_mode`, or `cmd notification set_dnd` | `global.zen_mode` plus the notification service's reported mode | Low. May silence real calls during the test; restore promptly |

## Correction (2026-10-03)
An earlier version of this table listed Wi-Fi as DL-0309/0310 and Bluetooth as DL-0042/0043. Those are the **"Wi-Fi scanning"
and "Bluetooth scanning"** settings (scan while the radio is off); their `message` says "Disable WiFi" / "Enable Bluetooth", which is
the catalog's known misleading-message trap. The real toggles are DL-0573/0574 (Wi-Fi) and DL-0494/0495 (Bluetooth). Always
check `validation.key` and `qna_description`, never `message`. The full, extended list is in `docs/laya-implementation.md`.

## Held back, and why
- **Adaptive power saving** (DL-0399/0400) and **Eye comfort shield** (DL-0039/0040): good candidates, but the first overlaps with #1 and the second is cosmetic.
- **Auto rotate** and **font size**: easy to read and write, but the catalog has no entry for them, so the Doctor couldn't map a complaint to them.
- **App permissions** (revoking unused ones, the roadmap idea): not in this list on purpose. It needs different safety rules and a different catalog area, and nobody has tested it.
- **Motion smoothness and touch sensitivity** stay read-only until an effect probe exists for them.

## Suggested order
#2 and #3 first (cheapest, clean probes), then #6, then #4/#5, then #1 last because of its side effects.
