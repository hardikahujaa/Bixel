# Bixel — the 5-day plan

25 Sept to 30 Sept 2026. Read [`KIT_NOTES.md`](KIT_NOTES.md) before you write any
code.

Each day below is self-contained apart from a handful of facts every day needs.
Those are in the block at the top. **If you hand one day to someone (or paste it
into a fresh Claude Code session), send the block with it.**

---

## Paste-this-with-any-day block

```
PROJECT: Bixel - Samsung PRISM GenAI Hackathon, Theme 2 (Guided Troubleshooting).
Repo: https://github.com/hardikahujaa/Bixel.git   Deadline: 30 Sep 2026.
Kit files live in student_kit/: schema.py, siis_responses.json (20 rows),
deeplinks.json (578 entries), sample_output.json, input.txt.
Stack: Python 3.10+, FastAPI, Pydantic v2, Gemini for the LLM (key in .env,
never committed). NO auth on the API - judges call it with no key or headers.

WHAT WE BUILD: POST /v1/troubleshoot takes {query, siis_response:{title,content}}
and returns a ContextDeeplinkResponse. GET /health returns exactly {"status":"ok"}.

RESPONSE SHAPE (from student_kit/schema.py - that file is the grader's copy):
contexts: [ Goal ]
Goal   = { goal, title, score, actions: [Action] }
Action = { actionName, description, category, stepGroups: [StepGroup] }
StepGroup = { steps: [str], actionableDeeplink: Deeplink|null,
              validationDeeplink: ValidationDeepLink|null }
Deeplink = { deeplink, description, message, originalType }   (classes stays null)
ValidationDeepLink = { deeplink, key, resultType, condition, value }  (key REQUIRED)
category is one of: auto | manual | critical
NOTE: the class is spelled ValidationDeepLink, capital L.

FORMATTING RULES (schema.py does NOT enforce any of these - we enforce them):
- goal  = "Follow these steps to perform this <Name> Troubleshooting."
          or "...Configuration."  WITH the trailing period. Keep it in ONE constant.
- title = exactly 2-3 words
- Action.description = exactly 5-7 words AND starts with "It will"
- score = float between 0.0 and 1.0
- steps must be non-empty and traceable to the siis_response content. Never invented.
- ZERO URLs anywhere in the output. No http, www., .com, .html, markdown links.
- Any action with category "auto" MUST have an actionableDeeplink.

DEEPLINK RULE (verified against sample_output.json):
On a catalog match we invent nothing. Take the deeplinks.json entry and copy
deeplink / description / message / originalType verbatim into actionableDeeplink.
Drop id, control_type, qna_description. Copy the entry's whole "validation"
object verbatim into validationDeeplink. If nothing matches, use
"bixby://dummy_positive" and write your own description + message (5-7 words
naming the concrete Settings screen). The LLM must NEVER emit a URI - it picks
a catalog id or null, and we look the URI up.

FOUR GATES. Miss one and the whole automated score is zero:
G2 /health returns {"status":"ok"}   G3 >=95% of test queries in our results file
G4 >=90% of responses schema-valid   G5 zero URL leaks anywhere

TWO LANDMINES:
1. siis_responses.json rows row_3, row_11 and row_17 contain "samsung.com" in
   their content, despite the file claiming it's URL-free. Copy a step verbatim
   from those and we fail G5. Everything that goes out must be sanitized.
2. schema.py has zero validators, so {"contexts": []} is "schema-valid".
   Returning empty contexts is BANNED - it passes the gates and scores zero
   on generalization. Every error path returns a real, non-empty answer.

Row ids are NOT contiguous: row_1..row_5, row_7..row_17, row_19..row_22.
Read and write every file as UTF-8 - row_11 has an em dash that will corrupt.
```

---

## Who owns what, all five days

So nobody ever builds the same thing twice:

- **M1** — the deeplink matcher, and the LLM pipeline that turns SIIS text into
  goals/actions/steps. Plus the AI Usage Disclosure at the end.
- **M2** — the strict validator, the sanitizer, the catalog to Deeplink
  projection, the cache, all the speed work, and the Dockerfile.
- **M3** — the FastAPI service, the deployment and public URL, the test harness
  that scores our gates, `results.jsonl`, and the README.
- **M4** — the query paraphrases, the made-up test payloads, the demo page, the
  video, the deck.

Nobody touches anybody else's file. If you think you need to, say so out loud
instead.

---

# Day 1 — Thursday 25 Sept (today, half a day)

## These have to happen in order, first

**Step one, 20 minutes, one person (M3).** The GitHub repo was empty — zero
branches — and the local folder was not a git repo at all; the kit arrived through
OneDrive. So: initialise the repo, commit `student_kit/` as-is, commit a
`.gitignore` with `.env*` in it **before** anyone gets an API key. Push to `main`.
**Hand off:** tell the other three it is up, they clone.

**Step two, 90 minutes, all four on a call.** This is the only meeting that
matters all week. You are agreeing on shapes, not writing code.

The seven function shapes everyone builds against:

```
match_deeplinks(step_text, context)   -> [{catalog_id, score, entry}]          (M1)
to_deeplink_pair(catalog_entry)       -> (Deeplink, ValidationDeepLink|None)   (M2)
sanitize(text)                        -> text                                 (M2)
validate(response)                    -> {ok: bool, errors: [str]}             (M2)
extract(query, siis_response)         -> ContextDeeplinkResponse               (M1)
cache.get_or_compute(query, siis, fn) -> response                             (M2)
variations(query)                     -> [str], 8 to 10 of them                (M4)
```

Then M2 writes three golden example responses into `fixtures/` before the call
ends. One normal answer, one with a `dummy_positive` deeplink, one with a
`critical` action and null deeplinks. M2 writes them because M2 owns the
validator, so they will write examples that actually exercise the rules. Everyone
else codes against these files today.

And decide these five out loud:

1. The `query` field in our results file is `original_query` from
   `siis_responses.json`, copied character for character. Not the line from
   `input.txt` — those differ on two rows.
2. LLM is Gemini. **Somebody gets the key in this meeting.** Nothing downstream
   works without it. Name who.
3. Cache key = embedding of the query's intent, plus a hash of the SIIS content.
   Not an exact string match.
4. Returning `{"contexts": []}` is banned. Say it out loud so it is not debatable
   at 1am on Sunday.
5. Trailing period on `goal` is in. One constant, one place.

**Hand off:** M2 pushes `fixtures/`. M3 cannot build the endpoint response without
it.

## Now everyone works at the same time

Nothing below waits on anyone else's code. Here is exactly why, per person.

### M1 — build the matcher

*Safe in parallel because it only reads `student_kit/deeplinks.json` and touches
nothing anyone else owns.*

Load all 578 entries. For each one build a text blob from `qna_description` +
`message` + `description` and embed it. `qna_description` is the most useful of
the three — it is written as a user intent, like "Backs up your device data to
Samsung Cloud so you can restore it if your device is lost." `message` is a short
label like "Disable Adaptive Display". Build a simple similarity index over those.

Then the part people skip and should not: open the SIIS documents, pull out about
30 real step sentences, and hand-label which catalog id each one should match —
including several that should match **nothing**. Measure precision on that set.

The whole value of this matcher is knowing when to shut up: a wrong match costs us
points, and a wrong match promoted to an `auto` action costs more. So it returns a
score and refuses below a threshold. That labelled set is the only way to pick the
threshold honestly.

One coverage fact to save you time: the catalog is all Settings toggles. There are
entries for display, brightness, accessibility, navigation bar, edge panels, multi
window, screen zoom, touch sensitivity, screen timeout, reset options, backup.
There is **nothing** for safe mode, clearing cache, Smart Switch, screen
mirroring, or the floating assistant menu — which is exactly what a lot of the
SIIS steps tell people to do. So plenty of steps legitimately get no deeplink.
That is fine and expected.

### M2 — build the validator and the sanitizer

*Safe in parallel because it only needs `student_kit/schema.py` and the formatting
rules, both of which exist right now.*

The validator takes a response dict and returns every rule it breaks. Check all of
it: the goal string against the regex, title is 2–3 words, every description is
5–7 words and starts with "It will", score is between 0 and 1, every `steps` list
is non-empty, `category` is actually present (it is optional in `schema.py` and
silently defaults to `manual`, which will hide bugs), every `auto` action has an
`actionableDeeplink`, and a hard scan for anything URL-shaped.

The URL scan also checks that every `bixby://masked/act/...` URI is one of the 578
real ones, and every `bixby://masked/val/...` is one of the 419 validation URIs.
Those are two separate lists with zero overlap — build both.

Your first test: run the validator on `student_kit/sample_output.json`. It
**should fail**. That sample's two descriptions are 9 and 12 words, well over the
5–7 limit. If your validator says that file is fine, your validator is wrong.

Then the sanitizer. It takes any string heading for the output and strips
URL-shaped tokens. It exists because three SIIS rows contain `samsung.com` and one
leak zeroes our entire automated score.

### M3 — get a live API up today

*Safe in parallel because you serve `fixtures/` from M2 and call nothing real.*

FastAPI app. `GET /health` returns exactly `{"status": "ok"}`, nothing else, no
auth anywhere in the app — judges call it raw. `POST /v1/troubleshoot` accepts
`{query, siis_response}` and returns one of M2's fixtures.

Then **deploy it today.** Render, Railway, Fly, whatever is fastest — do not wait
for a Dockerfile, M2 is not writing that until Day 4 and you do not need it. Hit
the public `/health` from your phone and confirm it answers. That is gate G2
closed on day one, and it means the scary "is it reachable for judges" question is
answered before we have written anything real.

### M4 — paraphrases and fake test cases

*Safe in parallel because it only reads `student_kit/siis_responses.json` and
produces new files nobody else is editing.*

First job: for each of the 20 queries, write 8–10 paraphrases. Not 7, not 11 — the
count is scored. They have to be genuinely different: change the vocabulary, the
sentence structure, the formality. "My Galaxy S22 screen is completely black" and
"S22 display won't turn on at all" and "phone powers up but nothing shows on
screen." Write a small function that scores how lexically different a set is, so
you can tell when you have written ten ways of saying the same sentence with the
same words.

Second job, and this is the one that actually earns points: start writing 6–8
**fake SIIS payloads** for problems that are not in the kit. Same
`{title, content}` shape, similar markdown-ish style with `##` headings and short
instruction lines. Deliberately pick other areas — battery draining, Wi-Fi
dropping, camera not focusing. The judges test us on payloads we have never seen,
and the 20 in the kit cannot tell us whether that works. Only yours can.

## Where Day 1 should end

A public `/health` answering. A validator that correctly rejects the kit's own
sample. A matcher with a real precision number. Paraphrases underway. Nobody
blocked on anybody.

---

# Day 2 — Friday 26 Sept

Today's goal is narrow and it is the whole point of the day: **all four gates
green, on the deployed API, with all 20 queries.** Points come later. Gates first.

## Everyone works at the same time, first half of the day

### M1 — write the extraction pipeline

*Safe in parallel because it is a standalone function that takes a query and a
SIIS payload and returns a dict. It does not import FastAPI and it does not touch
the API.*

`extract(query, siis_response)` reads the SIIS `title` and `content` and produces
goals, actions and steps. Put the hard rules in the prompt itself: return JSON
only, steps must be lines lifted from the supplied content or very close to it,
never invent a step, never output a URL, never output a URI of any kind.

That last one matters. **The LLM picks a `catalog_id` or `null`, never a URI.** The
actual `bixby://` string gets looked up afterwards. That is what makes hallucinated
deeplinks structurally impossible instead of something we have to catch.

Get it working on 5 of the 20. Make one of them a **bad match** on purpose —
`row_8` or `row_12`. `row_8`'s query is "my Flip 7 inner screen is dead" and the
SIIS document it comes with is about *screen mirroring to a Samsung TV*. `row_12`
is someone asking how to remove a floating shortcut circle, paired with a document
about *Multi window and App pairs*. Several rows are like this. The system has to
produce a grounded, valid, useful-looking answer anyway, using only the text it was
handed. If your prompt only works on the rows where the document actually matches
the question, it will fall apart on the hidden test cases.

One more thing you will hit: only about 10 unique documents cover the 20 queries.
"Blank or black display on a Samsung phone or tablet" appears six times, byte for
byte identical. So the same document has to yield different goals and titles
depending on the query, or six of our twenty answers will be the same answer.

### M2 — cache, and the pieces M3 is waiting on

*Safe in parallel because these are pure functions with agreed shapes. The cache
wraps a function it is handed; it does not care what that function is.*

Finish `to_deeplink_pair(catalog_entry)` — the projection. Given a `deeplinks.json`
entry, return the `Deeplink` (copy `deeplink`, `description`, `message`,
`originalType` verbatim; leave `classes` null) and the `ValidationDeepLink` (copy
the entry's whole `validation` object as-is). 432 entries have validation with just
`{deeplink, key}`, 138 have the full five fields, 8 have none. When the five are
present they are always `boolean` / `equal` / `"True"` — you never have to make
those up.

Then the cache. Key on the query's intent embedding plus a hash of the SIIS
content, so a paraphrase of the same problem hits the same entry. Count hits and
misses and expose those counts — they are directly scored and we will need the
numbers on Day 3.

### M3 — build the harness

*Safe in parallel because it drives the API over HTTP and the API is already
returning fixtures. It does not need anyone's real logic to be written.*

A script that reads all 20 rows from `siis_responses.json`, POSTs each to the
deployed API, writes `results.jsonl` with one object per line —
`{"query": ..., "query_variations": [...], "response": {...}}` — and then prints a
scorecard: does `/health` return exactly `{"status":"ok"}`, are >=95% of queries
present, are >=90% of responses schema-valid, are there zero URL leaks. Plus the
formatting rules and the paraphrase count.

This is the most valuable thing anyone builds this week. It tells us our automated
score before we submit instead of after.

### M4 — finish both files

*Safe in parallel because it is still only reading the kit and writing your own
files.*

All 20 queries x 8–10 paraphrases, done and committed. All 6–8 fake SIIS payloads,
done and committed. Then start the demo page — a single page that calls the live
API and shows the answer nicely, plus the gate scorecard. It is for the video, not
for the grader, so it does not need to be clever.

## Then, in this order, second half of the day

This is the day the pieces actually meet. Three handoffs, one after another:

1. **M2 to M3.** M2 pushes `validate()`, `sanitize()` and `to_deeplink_pair()`. M3
   cannot wire the real response path without these three. *What is handed off:
   three importable functions with the agreed signatures.*
2. **M1 to M3.** M1 pushes `extract()`, returning a dict with `catalog_id` or
   `null` on each step group — no URIs. *What is handed off: one importable
   function, and confirmation of which 5 rows it has been tested on.*
3. **M3 assembles.** Replace the fixture with: call `extract()`, then use
   `to_deeplink_pair()` to fill in every deeplink from the catalog id, then run
   everything through `sanitize()`, then `validate()`. Wrap the whole thing in M2's
   cache. Deploy. Run the harness against the live URL.

M3 owns the assembly step. Nobody else writes it — that is the wiring, and
splitting wiring between two people is how you get merge conflicts at midnight.

## Checkpoint, end of Day 2 — this one is hard

All 20 queries through the **deployed** API. `results.jsonl` generated. **All four
gates green in the harness.** If they are not green, tomorrow starts with that and
nothing else.

Then M4 records a rough two-minute screen capture of it working. It will look bad.
Record it anyway — a rough demo that exists beats a beautiful one that does not.

---

# Day 3 — Saturday 27 Sept

Gates are green, so today is about points and about not breaking on things we have
not seen. **Everything gets frozen tonight.**

## Everyone works at the same time, most of the day

### M1 — make the matcher and the pipeline better

*Safe in parallel because you are tuning your own two modules behind interfaces
that do not change.*

Tune the abstain threshold against your labelled set from Day 1. You want the
catalog-match rate up and precision held — a bad match is worse than no match.
Double-check that every single action you label `auto` has a deeplink attached,
because an `auto` with a null deeplink is an automatic deduction.

Then handle `row_19`. Its query is actually three separate complaints stuffed into
one string: the screen is cracked at the fold, touch does not work in places, and
the display is hard to see. That probably wants more than one Goal in `contexts[]`.
Worth getting right because it is the kind of input a real user actually sends.

### M2 — today is the speed day

*Safe in parallel because it is measurement and caching inside your own module.*

Four numbers, measured by a script, not estimated by feel:

- repeat query, 95th percentile, at or under **300 ms**
- cache hit rate at or above **90%** on repeats
- paraphrase hit rate at or above **80%** — feed it M4's paraphrases, that is what
  they are for
- cold start, 95th percentile, at or under **8 seconds**

The two things that will actually get you there: load the embedding model once at
startup, never per request. And embed the 578 catalog entries at build time, ship
the index as a file, and load the file — do not compute it on boot or your cold
start is gone before you have done anything.

### M3 — make the ugly paths behave

*Safe in parallel because it is error handling inside the service you already own.*

Every failure returns a schema-valid, **non-empty** answer. LLM times out, LLM
returns garbage, LLM is rate-limited, SIIS content is nearly empty — all of them
still produce a real response. If you have nothing else, build a grounded answer
out of the SIIS `##` headings. Never `{"contexts": []}`. Add a timeout and one
retry on the Gemini call.

### M4 — finish the visible stuff

*Safe in parallel because the demo page only consumes the live API, and the deck is
your own file.*

Demo page finished. Deck skeleton started. And write the demo click-path down as a
numbered list — the exact sequence someone performs on camera. Doing this now,
while things are calm, is why the Day 4 recording takes twenty minutes instead of
three hours.

## Then, in this order

1. **M4 to M3.** M4 hands over the fake SIIS payload file. *M3 cannot test
   generalization without it.*
2. **M3 runs the harness against those payloads.** This is the only honest test of
   whether we survive the hidden scenarios. Expect it to find things. That is the
   point of doing it today and not Monday.
3. **M1 to M3 and M2 to M3.** Retuned matcher, and the cache with the measurement
   script, both pulled into the live service and redeployed.
4. **Everyone fixes what the harness found.**

## Checkpoint, end of Day 3

Harness green on the 20 kit rows **and** on M4's invented payloads. M2's four speed
numbers measured and inside target. Then **feature freeze** — cut a git tag. From
here it is bugs, docs, video, deck. Nothing new, including the small thing that
will only take a minute.

---

# Day 4 — Sunday 28 Sept

No new features. Today is packaging and recording.

## Everyone works at the same time, morning

- **M2 — Dockerfile and compose.** *Safe in parallel because it is new files nobody
  else touches.* One command should bring the service up locally, because judges
  will try. Also check the deployment's cold-start behaviour — free hosting tiers
  put the app to sleep, and a sleeping app blows the 8-second cold-start target. A
  cron ping every 10 minutes fixes it; add it.
- **M3 — README and the final results file.** *Safe in parallel because both are
  your own files.* README gets the exact commands, copy-pasteable, the live
  endpoint URL, and an honest line saying the deeplink URIs are the kit's masked
  catalog values copied verbatim. Then regenerate `results.jsonl` from the frozen
  build and commit it.
- **M1 — the disclosure.** *Safe in parallel because it is a document, not code.*
  Be specific: Gemini was used for extracting goals/actions/steps from SIIS text,
  and for drafting paraphrases. Hand-built: the matcher, the validator, the
  sanitizer, the projection, the cache, the harness. That honesty is worth more
  than hedging.
- **M4 — get ready to record.** *Safe in parallel because you are setting up, not
  yet depending on a frozen build.* Script, screen layout, browser tabs, audio
  check.

## Then, in this order

1. **M2 to M3 (or M4).** M2 hands over the Dockerfile. Someone who is **not** M2
   does a fresh clone into an empty folder and runs it. Nobody verifies their own
   container — that is how hidden local state gets shipped.
2. **M2 to M1.** M2 hands M1 the four measured speed numbers. M1 cannot finish the
   numbers section without them.
3. **M1 to M4.** M1 hands M4 the disclosure and the numbers. M4 cannot finish the
   deck without them.
4. **M3 to M4.** M3 confirms the deployment is frozen and stable. *Only then does
   M4 record.* Recording against a service someone is still pushing to is wasted
   footage.
5. **M4 records the real demo.** Two clean takes. Show `/health`, a live call, a
   paraphrase hitting the cache, and the harness scorecard going green.

## Gate for the day

Someone outside the project reads only the README and gets to a working API call in
under five minutes. If they cannot, the README is wrong, not them.

---

# Day 5 — Monday 29 Sept

Everything finishes tonight. Tomorrow is for submitting, not working.

## Everyone works at the same time

- **M4 — cut the video to under 5 minutes.** *Safe in parallel: raw footage is
  already in hand.* Then finalise the deck, named `CollegeName_TeamName`.
- **M3 — final harness run.** *Safe in parallel: your own scripts.* Run it against
  the deployed URL, confirm `results.jsonl` in the repo matches what the live API
  returns, and screenshot the scorecard.
- **M2 — verify the two ways judges will actually reach it.** *Safe in parallel:
  verification, not changes.* Docker up from a clean clone, one more time. And hit
  the deployed `/health` and `/v1/troubleshoot` **from a phone on mobile data** —
  not from your laptop on the same Wi-Fi as everything you have been testing.
- **M1 — finalise the disclosure** and sanity-read the whole repo as a stranger
  would. *Safe in parallel: reading and one document.*

## Then, in this order

1. **M3 to M4.** Scorecard screenshot goes into the deck.
2. **M3 pushes the final `results.jsonl`**, makes the repo public, and pushes the
   release tag. Last write to the repo.
3. **All four read the deliverables list out loud together** and tick each item
   against the actual repo. Ten minutes, catches the thing everyone assumed someone
   else did.

---

# Tuesday 30 Sept — submit by midday

Submit with hours to spare, not minutes. Then **somebody owns "is the endpoint
still answering"** for the rest of the evaluation window. Pick that person today,
not on Tuesday.

---

## If you finish early or get stuck

Grab one of these instead of inventing work: more hand-labelled matcher pairs;
more fake SIIS payloads; more validator tests; deck slides; the paraphrase
diversity checker; the keep-alive ping; **a second LLM provider behind a flag**, in
case Gemini rate-limits us in the middle of judging. That last one is cheap
insurance for a live demo.

## Problems we already know about, and what we do about them

| What could go wrong | What we already decided |
|---|---|
| `samsung.com` sits in rows 3, 11 and 17, so G5 zeroes everything | Sanitizer on everything leaving the service, plus a URL scan the harness runs on every response. Built Day 1. |
| Gemini rate-limits or dies during judging | Cache pre-warmed with all 20 queries and every paraphrase. Second provider behind a flag. On a miss, a grounded answer built from SIIS headings — never empty. |
| Free hosting sleeps and wrecks the cold-start number | Keep-alive ping every 10 minutes. |
| We quietly start leaning on `dummy_positive` for everything | Harness reports the real catalog-match rate every run, so it cannot drift without us seeing it. |
| Someone adds `return {"contexts": []}` as a safe fallback at 2am | Banned on Day 1, and the harness fails the run if it sees it. |
| Two people edit the same file | Four owners, listed at the top. If you need someone else's file, say it out loud instead of editing it. |
