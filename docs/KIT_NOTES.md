# Kit notes — read this before writing code against `student_kit/`

Findings from reading all five kit files end to end. Several of these are not
documented in the kit itself. They live here so three people don't each
rediscover them on Saturday night.

---

## 1. `schema.py` enforces nothing

It is plain Pydantic with **zero validators** — no regex, no word counts, no
bounds on `score`, no `extra="forbid"`. So all of the following are
"schema-valid":

```python
{"contexts": []}
{"contexts": [{"goal": "x", "title": "y", "score": 5.0, "actions": []}]}
```

Consequences:

- Gate **G4** (>=90% schema-valid) is a nearly free gate.
- Every formatting rule — the goal regex, 2–3 word title, 5–7 word description,
  score bounds — is scored separately by the grader under **A1**. Passing
  `schema.py` proves almost nothing.
- **Returning `{"contexts": []}` is banned.** It sails through G4 and G5 while
  scoring zero on generalization. Every error path must return a real, non-empty
  answer. If there is nothing else to go on, build one from the SIIS `##`
  headings.

Small traps in the same file:

- The class is spelled **`ValidationDeepLink`** — capital L in "Link".
- `ValidationDeepLink.key` is **required**.
- `Deeplink.classes` exists (`Optional[Dict[str, str]]`), is in no spec, and is
  never present in the catalog. Leave it `null`.
- `Action.category` is **optional and defaults to `manual`**. A bug that drops
  the field does not raise — it silently downgrades an `auto` action. Our
  validator requires it explicitly.
- `ResultTypes` maps oddly: `boolean="boolean"`, `intNum="integer"`,
  `string="str"`, `floatNum="float"`. Only `"boolean"` ever appears in practice.

---

## 2. The URL landmine — this one can zero the whole score

`siis_responses.json` claims its text is "pre-cleaned: no URLs or images."

It isn't. **`row_3`, `row_11` and `row_17` all contain `samsung.com` in their
`content`.**

Gate **G5** is zero URL leaks anywhere in the output, and missing any gate zeroes
the entire automated score. Copy a step verbatim out of one of those three rows
and we are done. Everything leaving the service goes through a sanitizer, and the
harness scans every single response.

The catalog, by contrast, is clean — zero URL-shaped text across all 578 entries
and all three of their text fields. Copying catalog strings verbatim is safe.

---

## 3. The deeplink rule, verified against `sample_output.json`

I checked the sample's `bixby://masked/act/b3ed3ed663` against the catalog. It is
`DL-0542`, and the sample's `description`, `message` and `originalType` are
**byte-identical copies** of that entry's fields. Its `validationDeeplink` is
that entry's whole `validation` object, copied unchanged.

So the rule is mechanical:

```
actionableDeeplink  = catalog entry projected onto
                      {deeplink, description, message, originalType}
                      (drop id, control_type, qna_description; classes stays null)

validationDeeplink  = the entry's "validation" object, copied verbatim
```

On a catalog match **we invent nothing at the deeplink level**. The only text we
write is `goal`, `title`, `score`, `actionName`, `Action.description` and `steps`.

If nothing matches: use `bixby://dummy_positive` and write your own `description`
and `message` — 5–7 words naming the concrete Settings screen. That entry's own
`qna_description` says exactly this.

**The model never emits a URI.** It picks a catalog id or `null`, and we look the
URI up afterwards. That makes a hallucinated deeplink structurally impossible
rather than something we have to catch after the fact.

---

## 4. Catalog shape (`deeplinks.json`, 578 entries)

Fields: `id`, `deeplink`, `description`, `message`, `originalType`,
`control_type`, `qna_description`, `validation`.

- 578 unique URIs: 577 of the form `bixby://masked/act/<10 hex>`, plus the single
  `bixby://dummy_positive`.
- Validation URIs are a **separate namespace**: `bixby://masked/val/...`, 419
  unique, **zero overlap** with the act URIs. A "did we invent a URI" check needs
  **two allowlists, not one**.
- `validation` shapes: 432 entries have `{deeplink, key}`; 138 have the full
  `{deeplink, key, resultType, condition, value}`; 8 are `null`. When the full
  five are present the values are **always** `boolean` / `equal` / `"True"` —
  nothing to synthesise.
- `originalType`: `onClickURL` 254, `offURL` 138, `onURL` 138, `updateURL` 36,
  `null` 11, `placeholder` 1.
- `control_type`: `2` (278), `null` (264), `3` (21), `5` (15). Not part of the
  response schema — don't emit it. Usable as a matcher feature.

**What to match on:** `qna_description` is written as a user intent and is the
best embedding target. `message` is a short imperative label ("Disable Adaptive
Display"). `description` is boilerplate-shaped ("Opens the X settings page in
device Settings on the device").

**Coverage reality.** The catalog is all Settings toggles. Keyword hits: display
44, power/charging 26, brightness 12, accessibility 8, navigation bar 8,
diagnostics 6, edge panels 4, multi window 4, dark mode 3, storage 3, screen zoom
2, touch sensitivity 2, screen timeout 2, reset options 2, backup 2, motion
smoothness 1. There is **nothing** for safe mode, clearing cache, Smart Switch,
screen mirroring, or the floating assistant menu — which is exactly what many
SIIS steps instruct. So plenty of steps legitimately get no deeplink, and that is
correct behaviour, not a gap.

Which drives the category rule: **label an action `auto` only when we actually
hold a match.** Everything else is `manual` or `critical` with a null deeplink.
An `auto` action with no `actionableDeeplink` is an automatic deduction.

---

## 5. `siis_responses.json` — 20 rows, and what is odd about them

Shape: `{_readme, count: 20, responses: [{id, original_query, siis_response: {title, content}}]}`.
`siis_response` only ever has those two keys.

- **Row ids are not contiguous**: `row_1`–`row_5`, `row_7`–`row_17`,
  `row_19`–`row_22`. `row_6` and `row_18` do not exist. Never index by number.
- **All 20 queries are screen/display problems** — blank, black, flickering,
  cracked, half-dark, laggy touch, distorted, a floating shortcut circle, a
  display that will not fill the screen. A useful hint about the hidden test
  scenarios.
- **Only ~10 unique documents cover the 20 queries.** "Blank or black display on
  a Samsung phone or tablet" appears 6x byte-identical (2648 chars each), "Some
  things to check first" 3x, "Use Multi window and App pairs" 2x, "Cracked or
  bleeding screen" 2x. Two consequences: caching on a content hash is nearly
  free, and the same document must yield **different** goals and titles per query
  or six of our twenty answers will be identical.
- **Several documents barely match their query**, which is the real difficulty:

  | Row | Query is about | Document is about |
  |---|---|---|
  | `row_1` | screen flashes when opening Gmail | Email server not responding |
  | `row_8` | Flip 7 inner screen is dead | Screen mirroring to a Samsung TV |
  | `row_12` | removing a floating shortcut circle | Multi window and App pairs |
  | `row_20` | distorted screen, wants a diagnostic | Screen does not rotate |

  This is exactly what **A4** tests. Ground the steps in the supplied text even
  when it is a poor match. Do not answer from world knowledge.

- **`row_19` is three complaints in one string** (cracked at the fold, dead touch
  zones, cannot see the display). Probably wants more than one `Goal` in
  `contexts[]`.
- `row_16` has a masked model name, `S***** Ultra`.
- `row_11` contains a UTF-8 em dash. **Read and write every file as UTF-8** or
  that one character will corrupt a line on Windows.
- Content is markdown-ish: `#` / `##` headings and short newline-separated
  instruction lines ("Navigate to Settings.", "Tap Apps."). Steps are close to
  directly extractable.

---

## 6. `sample_output.json` — trust it for shape, not for formatting

Top level is `{query, response}` — **no `query_variations`**, so it is not the
submission line format. The submission stays
`{"query": ..., "query_variations": [...], "response": {...}}`, one object per
line.

It is authoritative on nesting and on the deeplink mechanics (section 3 above).

It is **not** authoritative on formatting. Its two `Action.description` values are
**9 and 12 words**, against a 5–7 word rule, and the second is cut off mid-phrase:

```
"It will facilitate secure data transfer between your devices"              (9)
"It will help you locate the nearest Samsung service center and schedule"   (12)
```

A correct validator **rejects this file**. That is the first test to write: if the
validator passes `sample_output.json`, the validator is wrong.

Note also that its `goal` has **no trailing period**, which contradicts the
written spec. We decided to include the period. Keep it in one constant so every
output can be regenerated by flipping one value:

```
"Follow these steps to perform this <Name> Troubleshooting."
"Follow these steps to perform this <Name> Configuration."
```

---

## 7. `input.txt` — there is no extra information in it

It is just the 20 queries as plain text, one per line. No ids, no JSON, no SIIS
text, no instructions. I diffed all 20 against `original_query`: 18 are
byte-identical, line 1 is missing the `"1. "` prefix the JSON has, and line 17
collapses the JSON's internal newlines into spaces.

So it is a human-readable checklist and nothing more. Its one hazard is tempting
you to build the `query` field from it.

**Use `original_query` from `siis_responses.json`, character for character** — it
is the machine-readable field the grader most plausibly keys G3 on.

---

## 8. The formatting rules, in one place

- `goal` = `"Follow these steps to perform this <Name> Troubleshooting."` or
  `"...Configuration."` — with the trailing period, from one constant.
- `title` = exactly 2–3 words.
- `Action.description` = exactly 5–7 words **and** starts with `"It will"`. This
  rule is for the *action*, not for `Deeplink.description` — catalog descriptions
  are copied verbatim and do not start with "It will".
- `score` = float in `[0.0, 1.0]`.
- `steps` non-empty, and traceable to the `siis_response` content. Never invented.
- Zero URLs anywhere: no `http`, no `www.`, no `.com`, no `.html`, no markdown
  links.
- Every `auto` action has an `actionableDeeplink`.
- 8–10 query paraphrases per query. Not 7. Not 11.

---

## 9. The four gates

Miss one and the entire automated score is zero, with no partial credit.

| Gate | Requirement |
|---|---|
| **G2** | `GET /health` returns `{"status": "ok"}` |
| **G3** | >=95% of test queries appear in our results file |
| **G4** | >=90% of responses are schema-valid |
| **G5** | Zero URL leaks anywhere in the output |
