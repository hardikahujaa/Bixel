# Demo script — the exact click-path to record

For M4. Target: **under 5 minutes**. Write nothing new on camera; every scenario below is
already loaded in the page's dropdown.

**Open `https://bixel-mmvy.onrender.com/` and nothing else.** No terminal, no editor, no
Postman. The whole demo is one URL, which is itself part of the point.

Two things to do **before** you hit record:

1. **Wake the service.** Render's free tier sleeps. Load the page once, run one request, then
   reload. A cold wake mid-recording looks like a broken demo.
2. **Run one request and discard it**, so the model is warm. The first request of a process
   pays about a second of ONNX initialisation on top of the Gemini call.

---

## 1 · What it is (about 25 seconds)

Page is open, nothing clicked yet.

> "Samsung support agents spend around fifteen minutes searching a knowledge base to answer
> one complaint. Bixel takes the complaint and the knowledge-base document and returns a
> validated repair plan, where every step is traceable and every shortcut is a real Samsung
> deeplink. This page is the deployed service — there is nothing else running."

## 2 · The clean case — `row_21` (about 60 seconds)

It is selected by default. Read the complaint out loud: *"Galaxy S22 screen inputs are
delayed and the touch responsiveness is laggy."*

Click **Get repair plan**. While it runs:

> "It is matching the document's sections against Samsung's catalog of 578 deeplinks first,
> then asking Gemini to build the plan."

When it lands, point at **the steps** first — this beat fires every time:

> "Each of these is a line from the document we supplied, not something the model knew about
> Samsung phones. Anything it cannot trace back to the source text gets dropped before you
> see it."

**On the deeplink, read this before you record.** A deeplink will *sometimes* appear on this
step with an `auto` badge, and sometimes not. That is measured, not bad luck: shortcut
selection is non-deterministic even at temperature 0 — the same byte-identical request
produced a deeplink 3 times out of 3, then 0 times out of 3, then once again. So:

- **If a deeplink is on screen**, point at it: *"this URI is copied byte-for-byte from
  Samsung's catalog. The model never sees a URI and is never asked for one — it picks from a
  numbered list, so it cannot invent a link."* Then the note under the button: *"Samsung
  supplied the catalog with its URIs masked, so we copy and say so rather than faking a Bixby
  launch."*
- **If there is no deeplink**, do not re-run hoping for one and do not pretend. Say the true
  thing, which is a better answer anyway: *"most sections have no matching Settings shortcut —
  the catalog is Settings toggles and these documents mostly describe physical checks. The
  system says so instead of attaching a plausible wrong one. Zero wrong deeplinks across all
  twenty graded queries."*

Either way the point lands. The differentiator is that a wrong deeplink is impossible, not
that every step has one.

## 3 · Three complaints in one query — `row_19` (about 45 seconds)

Select `row_19`. Read it: cracked at the fold, dead touch areas, hard to see the display.

> "One query, three separate problems."

Click **Get repair plan** → **three separate goals come back**, one per complaint.

## 4 · The cache — reword it (about 40 seconds)

Go back to `row_21`, then edit the complaint box to reword it — *"my S22 is slow to respond
when I touch the screen"* — and submit.

> "Different words, same problem. That came back in about twenty milliseconds instead of
> seven seconds, because the cache matches on meaning rather than on the exact string. Zero
> cost, because no model call happened."

Point at the **metrics panel**: latency, hit rate, `$0.00`. These are live from `/metrics`,
not typed in.

## 5 · A document it has never seen (about 45 seconds)

Switch the dropdown to the **Unseen (generalization)** group and pick `unseen_bluetooth` or
`unseen_battery`.

> "Every one of the twenty graded queries is a screen problem. This is Bluetooth pairing, on
> a document we wrote ourselves that the pipeline has never seen — because the twenty kit
> rows cannot tell us whether it generalizes, since we tuned against them."

Submit. A valid, grounded plan comes back.

> "Eight of eight unseen documents pass, fully grounded."

## 6 · The close — the harness (about 40 seconds)

This is the one moment to leave the browser. One terminal, one command:

```bash
python -m scripts.run_harness --target https://bixel-mmvy.onrender.com
```

Let the scorecard fill in and hold on it:

```
[PASS] G2  /health returns exactly {"status": "ok"}
[PASS] G3  >=95% of test queries present in results.jsonl     20/20 = 100.0%
[PASS] G4  >=90% of responses schema-valid                    20/20 = 100.0%
[PASS] G5  zero URL leaks anywhere                            0 leaks
RESULT: ALL GATES PASS
```

> "Every gate, scored against the deployed service, before we submit rather than after."

---

## Say these, they are the differentiators

- The model **cannot** invent a deeplink — not "we validate them afterwards". It never sees
  a URI.
- Steps are **traceable to the supplied document**, which is what stops it answering from
  general knowledge.
- The service **never returns an empty answer**. If Gemini is down, a deterministic fallback
  builds a plan from the document's own headings.
- **Every number on screen is measured**, including the thresholds.

## Do not say

- Do not claim the deeplinks launch Bixby. They are masked placeholders and the page says so.
- Do not claim high deeplink coverage. It is about 2 of 35 step groups, close to the ceiling
  the catalog allows — and that is the honest answer if a judge asks.
- Do not read thresholds off a slide. If asked where 0.77 or 0.85 came from, the answer is
  "a sweep against hand-labelled data, and the numbers are in the module docstring."

## If something breaks on camera

- **Slow first request** — say so: "uncached, so it is paying a real model call."
- **A 503 from Gemini** — this is the best possible accident. The fallback still returns a
  valid plan; point at the score of 0.35 and say the service degrades honestly rather than
  failing.
- **The page will not load** — Render has gone to sleep. Stop recording, load it, wait, and
  start again from step 1.
