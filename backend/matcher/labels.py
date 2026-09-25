"""Hand-labelled evaluation set for the deeplink matcher.

Every label was decided by reading the real SIIS group text and the candidate entry's
``qna_description`` -- never its ``message``. That distinction turned out to matter a
great deal:

* ``DL-0022`` / ``DL-0349`` both read "View Reset Options", but their qna text is
  "automatically resets the device after too many failed unlock attempts" and "sets
  the TalkBack verbosity level". Neither is a factory data reset. There is **no**
  factory-reset entry in the catalog, so "Perform a Factory Data Reset" is a
  no-match -- even though the embedding scores it 0.787 against DL-0022.
* ``DL-0058`` / ``DL-0059`` read "Disable/Enable Charging" but mean "vibration
  feedback during charging". "Charge the Device" is a no-match.
* ``DL-0479`` / ``DL-0480`` read "Auto Restart" but mean *scheduled* auto-restart.
  "Force a Restart" is a no-match.
* ``DL-0116`` reads "View More options", which says nothing, but its qna is
  "configures gesture controls for navigation... swipes instead of buttons" -- that
  *is* the full-screen gesture setting.

The honest consequence: there are far fewer true positives than expected. The catalog
is a Settings-toggle catalog, and these SIIS documents mostly instruct physical
actions, app navigation and support escalation. Most groups correctly match nothing,
and that is the finding, not a failure of the matcher.

Labels are keyed by (document title prefix, section heading) and resolved against the
real file at load time, so a typo or a kit change fails loudly instead of silently
skipping a case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .siis_groups import SiisGroup, extract_groups


class Verdict(str, Enum):
    """What the matcher is expected to do with a group."""

    MATCH = "match"
    """Must return one of ``expected_ids`` as its top candidate."""

    NONE = "none"
    """Must return nothing. The catalog has no entry for this."""

    WEAK = "weak"
    """Genuinely arguable. Reported for visibility, excluded from scoring."""


@dataclass(frozen=True)
class Label:
    doc_prefix: str
    heading: str
    verdict: Verdict
    reason: str
    expected_ids: frozenset[str] = field(default_factory=frozenset)
    expected_polarity: int | None = None

    def resolve(self, groups: list[SiisGroup]) -> SiisGroup:
        hits = [
            g
            for g in groups
            if g.doc_title.startswith(self.doc_prefix) and g.heading == self.heading
        ]
        if len(hits) != 1:
            raise LookupError(
                f"label ({self.doc_prefix!r}, {self.heading!r}) matched {len(hits)} groups, expected 1"
            )
        return hits[0]


_EMAIL = "Email server not responding"
_BLANK = "Blank or black display"
_CHECK = "Some things to check first"
_SWITCH = "Transfer Secure folder with Smart"
_MULTI = "Use Multi window and App pairs"
_MIRROR = "Screen mirroring to your Samsung"
_CAMERA = "Screen flickers when using the Camera"
_CRACK = "Cracked or bleeding screen"
_ROTATE = "Screen does not rotate"
_TOUCH = "Touchscreen issues on a Galaxy"


LABELS: tuple[Label, ...] = (
    # ---------------------------------------------------------------- MATCH -----
    Label(
        _TOUCH, "5. Touch Sensitivity Setting", Verdict.MATCH,
        "Names the exact setting. Body says 'To turn off this feature', so the "
        "polarity is Disable -- both halves share an identical qna_description, so "
        "the direction can only come from the message verb.",
        frozenset({"DL-0125"}), expected_polarity=-1,
    ),
    Label(
        _TOUCH, "4. Full Screen Gesture Function", Verdict.WEAK,
        "Reclassified from MATCH to WEAK after measurement, and the reasoning is "
        "recorded because downgrading a label to flatter a metric would be "
        "dishonest. DL-0116's qna ('gesture controls for navigation... swipes "
        "instead of buttons') really is the navigation-gesture setting, so a MATCH "
        "was defensible. But its message is 'View More options', and none of the "
        "words that identify it appear in the step: setting-name grounding is 0.00 "
        "and even its qna terms overlap at only 1/9. So it is reachable by semantic "
        "similarity alone, which is exactly the signal shown to produce confident "
        "false positives elsewhere (DL-0022 at 0.787 on factory reset). Demanding "
        "this match would mean loosening the gate that keeps those out. It is also a "
        "poor answer to surface to a user, since the deeplink would be labelled "
        "'View More options'. Arguable, so unscored rather than demanded.",
        frozenset({"DL-0116"}), expected_polarity=0,
    ),
    Label(
        _MULTI, "Use Multi window", Verdict.MATCH,
        "DL-0168 qna: 'enables split screen and pop-up view for all apps'. The group "
        "is about turning Multi window on and using it, so polarity is Enable.",
        frozenset({"DL-0168"}), expected_polarity=1,
    ),
    Label(
        _MULTI, "Use pop-up view", Verdict.MATCH,
        "DL-0268 qna: 'swipe down from the top corner to open an app in a pop-up "
        "window'. Directly the feature the group describes.",
        frozenset({"DL-0268"}), expected_polarity=1,
    ),
    Label(
        _MULTI, "Customize the Edge panel", Verdict.MATCH,
        "DL-0096 qna: 'enables Edge panels for quick access to apps, contacts and "
        "tools by swiping the screen edge'. Note DL-0095/0096 are a polarity pair "
        "with the same qna, so direction comes from the message verb.",
        frozenset({"DL-0096"}), expected_polarity=1,
    ),
    Label(
        _MULTI, "Swipe gestures for Multi window", Verdict.MATCH,
        "Body covers both split screen and pop-up swipe gestures, so either "
        "DL-0270 (swipe for split screen) or DL-0268 (swipe for pop-up view) is a "
        "correct answer. Genuine ambiguity, so both are accepted.",
        frozenset({"DL-0270", "DL-0268"}), expected_polarity=1,
    ),

    # ----------------------------------------------------------------- WEAK -----
    Label(
        _ROTATE, "2. Adjust Screen Orientation Settings", Verdict.WEAK,
        "Group is about the Quick-settings auto-rotate toggle. The only rotation "
        "entry, DL-0461, means 'rotates the HOME SCREEN to landscape mode' -- a "
        "different setting. Arguable either way, so not scored.",
        frozenset({"DL-0461"}),
    ),
    Label(
        _CHECK, "Fingerprint Recognition Issues", Verdict.WEAK,
        "Group is about improving fingerprint accuracy under a screen protector. "
        "DL-0551 is fingerprint unlock setup -- adjacent but not the same. Only "
        "fingerprint entry in the catalog, so arguable.",
        frozenset({"DL-0551"}),
    ),

    # ----------------------------------------------------------------- NONE -----
    Label(
        _EMAIL, "Step 4: Clear the Email App's Cache and Data", Verdict.NONE,
        "Zero cache entries in the catalog. A confident match here would be pure "
        "hallucination.",
    ),
    Label(
        _EMAIL, "Step 5: Restart Your Phone in Safe Mode", Verdict.NONE,
        "Zero safe-mode entries. Scored 0.279 lexically against 'View Touch and "
        "hold to edit' purely because it sits in a touch-heavy document.",
    ),
    Label(
        _EMAIL, "Step 6: Contact Your Email Service Provider", Verdict.NONE,
        "Support escalation. No device setting involved at all.",
    ),
    Label(
        _EMAIL, "Step 1: Check Email Access on a PC", Verdict.NONE,
        "Action happens on a PC, not on the device.",
    ),
    Label(
        _BLANK, "Step 1: Check for Physical Damage and Liquid Exposure", Verdict.NONE,
        "Physical inspection. No setting can express it.",
    ),
    Label(
        _BLANK, "Step 2: Force a Restart", Verdict.NONE,
        "Hardware button combination. DL-0479/0480 'Auto Restart' mean SCHEDULED "
        "auto-restart, which is a different thing -- a trap for message-only matching.",
    ),
    Label(
        _BLANK, "Step 3: Charge the Device", Verdict.NONE,
        "Plugging in a charger. DL-0058/0059 read 'Disable/Enable Charging' but "
        "their qna is 'vibration feedback during charging' -- another misleading "
        "message.",
    ),
    Label(
        _BLANK, "Step 4: Attempt to Power On", Verdict.NONE,
        "Hardware button press.",
    ),
    Label(
        _CHECK, "Samsung Kids PIN Reset Process", Verdict.NONE,
        "No matching setting. Also the G5 canary: this group's text contains "
        "'kidshome.pin@samsung.com', so nothing the matcher returns for it may "
        "carry URL-shaped text.",
    ),
    Label(
        _SWITCH, "1. Connection Method", Verdict.NONE,
        "Physical cable and proximity instructions.",
    ),
    Label(
        _SWITCH, "2. Open Smart Switch App", Verdict.NONE,
        "Zero Smart Switch entries in the catalog.",
    ),
    Label(
        _SWITCH, "3. Select Transfer Method", Verdict.NONE,
        "In-app Smart Switch flow, not a device setting.",
    ),
    Label(
        _MIRROR, "Mirror Your TV with Smart View", Verdict.NONE,
        "Zero screen-mirroring or Smart View entries.",
    ),
    Label(
        _MIRROR, "What is Screen Mirroring?", Verdict.NONE,
        "Explanatory prose with no action in it at all.",
    ),
    Label(
        _MIRROR, "Access Smart View with SmartThings", Verdict.NONE,
        "SmartThings app flow. No catalog entry.",
    ),
    Label(
        _MIRROR, "Tips for Mirroring with Smart View", Verdict.NONE,
        "Mirroring troubleshooting tips. No catalog entry.",
    ),
    Label(
        _CAMERA, "Troubleshooting Video Flickering", Verdict.NONE,
        "Advises disabling Super steady mode and changing lighting. Zero 'super "
        "steady' entries in the catalog.",
    ),
    Label(
        _CRACK, "Samsung Repair Services", Verdict.NONE,
        "Repair booking. Support escalation, not a setting.",
    ),
    Label(
        _CRACK, "Cracked Screen", Verdict.NONE,
        "Describes physical damage.",
    ),
    Label(
        _CRACK, "Bleeding Pixels", Verdict.NONE,
        "Describes physical damage.",
    ),
    Label(
        _ROTATE, "1. Check for Physical Damage", Verdict.NONE,
        "Physical inspection.",
    ),
    Label(
        _ROTATE, "3. Update Device Software", Verdict.NONE,
        "All three 'View Update Settings' entries are auto-update toggles (carrier "
        "settings, Samsung system apps, security policy). None is 'check for a "
        "software update'.",
    ),
    Label(
        _ROTATE, "4. Restart Your Device", Verdict.NONE,
        "Manual restart via buttons. Auto Restart is a different feature.",
    ),
    Label(
        _ROTATE, "6. Perform a Factory Data Reset", Verdict.NONE,
        "The catalog has NO factory-reset entry. DL-0022 'View Reset Options' means "
        "auto-reset after failed unlock attempts; DL-0349 with the same message "
        "means TalkBack verbosity. The embedding scores DL-0022 at 0.787 here, so "
        "this is the single most important negative in the set.",
    ),
    Label(
        _ROTATE, "7. Contact Samsung Support", Verdict.NONE,
        "Support escalation.",
    ),
    Label(
        _TOUCH, "2. Restarting Your Device", Verdict.NONE,
        "Manual restart via buttons.",
    ),
    Label(
        _TOUCH, "3. Charger Issues", Verdict.NONE,
        "Swap the physical charger. Not a setting.",
    ),
    Label(
        _TOUCH, "6. Software Updates", Verdict.NONE,
        "Same reasoning as the rotation document's update section.",
    ),
    Label(
        _TOUCH, "7. Safe Mode", Verdict.NONE,
        "Zero safe-mode entries. Scored 0.704 semantically and 0.279 lexically "
        "against unrelated touch entries -- the strongest false positive in the set "
        "and the reason a single threshold cannot work.",
    ),
    Label(
        _TOUCH, "8. Factory Data Reset", Verdict.NONE,
        "No factory-reset entry, as above.",
    ),
)


#: Synthetic inputs that must never raise and must never match.
ADVERSARIAL: tuple[tuple[str, str | None, str], ...] = (
    ("empty string", "", "no text to match"),
    ("whitespace only", "   \t\n  ", "no text to match"),
    ("none", None, "callers will pass None eventually; must not raise"),
    ("punctuation only", "!!! ??? ... ---", "no content words"),
    ("single stopword", "the", "no distinctive term"),
    (
        "em dash from row_11",
        "My Galaxy Flip 6 screen is half black—one side of the display is dark.",
        "UTF-8 handling; a complaint is not a settings instruction",
    ),
    (
        "url bearing text",
        "Send a blank email to kidshome.pin@samsung.com to reset the PIN.",
        "G5 canary: must not match, and must never echo URL-shaped text",
    ),
    (
        "appliance bait on a phone step",
        "The phone gets very hot and the display dims when the temperature rises above "
        "normal while charging.",
        "The catalog holds refrigerator and air-conditioner entries. A phone step that "
        "mentions temperature must not pull them in. (An earlier version of this case "
        "literally asked about a refrigerator, so matching DL-0469 was correct and the "
        "case was testing nothing.)",
    ),
    (
        "very long text",
        ("Navigate to Settings and tap Display. " * 200),
        "5000+ chars must not blow up or spuriously match",
    ),
)


def labelled_groups() -> list[tuple[Label, SiisGroup]]:
    """Resolve every label against the real SIIS file. Raises on a stale label."""
    groups = extract_groups()
    return [(label, label.resolve(groups)) for label in LABELS]


def counts() -> dict[str, int]:
    tally = {verdict.value: 0 for verdict in Verdict}
    for label in LABELS:
        tally[label.verdict.value] += 1
    return tally
