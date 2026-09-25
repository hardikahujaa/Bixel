# Bixel

**Smart Guided Troubleshooting Engine** — Samsung PRISM GenAI Hackathon 2026–27, Theme 2 (Guided Troubleshooting).

Given a user's natural-language complaint about their Galaxy device, plus the
matching SIIS knowledge-base text, Bixel returns a structured, validated
troubleshooting plan in which every actionable step is linked to a real Galaxy
Settings deeplink taken from Samsung's provided catalog.

## Status

Day 2 of a 5-day build. Submission deadline **30 September 2026**.

| Piece | State |
|---|---|
| `GET /health` | **working** — returns exactly `{"status": "ok"}` |
| `POST /v1/troubleshoot` | **scaffold** — returns a schema-valid, non-empty placeholder; real pipeline lands at the Day 2 checkpoint |
| Deeplink matcher (`backend/matcher`) | **complete and measured** — precision 1.000, abstention 1.000 |
| Validator, sanitizer, projection, cache, extractor, variations | **typed stubs** — each names its owner and the rule it must satisfy |

See [`docs/PLAN.md`](docs/PLAN.md) for who is building what and when, and
[`docs/KIT_NOTES.md`](docs/KIT_NOTES.md) for the kit's traps — read that one before
writing any code against `student_kit/`.

## API

| Endpoint | Behaviour |
|---|---|
| `POST /v1/troubleshoot` | Takes `{query, siis_response: {title, content}}`, returns a `ContextDeeplinkResponse` |
| `GET /health` | Returns exactly `{"status": "ok"}` |

**No authentication on either endpoint.** Judges call them directly with no key
and no headers, so no auth middleware goes into this service.

## Setup

`python` on some machines resolves to a bundled interpreter with no pip — use an
explicit Python 3.10+ interpreter if `python -m pip` fails.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# POSIX:    source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env        # then put a real GEMINI_API_KEY in it
```

The matcher downloads a pinned 67 MB ONNX embedding model on first use, into
`backend/matcher/.model_cache`. Warm it once before serving:

```bash
python -c "from backend.matcher.matcher import get_matcher; get_matcher()"
```

### Run

```bash
python -m uvicorn app.main:app --reload
curl http://127.0.0.1:8000/health
```

### Test

```bash
python -m pytest -q                              # whole suite
python -m backend.matcher.evaluate               # matcher threshold sweep + scores
python -m scripts.measure_matcher_precision      # sentence-level dataset + rejected baseline
```

## Layout

```
app/                  FastAPI service and the agreed function stubs
  main.py             /health and /v1/troubleshoot            (M3)
  validator.py        formatting rules schema.py does not enforce (M2)
  sanitizer.py        strips URL-shaped tokens from output     (M2)
  projection.py       catalog entry -> Deeplink, verbatim      (M2)
  cache.py            intent + content-hash response cache     (M2)
  extractor.py        SIIS text -> goals/actions/steps via LLM (M1)
  variations.py       8-10 query paraphrases                   (M4)
  matcher_labels.json 29 sentence-level matcher labels
backend/matcher/      deeplink matcher, complete               (M1)
scripts/              measurement harness
student_kit/          Samsung's provided material, unmodified
docs/                 PLAN.md (who/when), KIT_NOTES.md (kit traps)
fixtures/             golden responses everyone codes against  (M2)
tests/                service and stub-contract tests
```

`backend/matcher/README.md` is the matcher's contract — import path, return shape,
thresholds and the traps it defends against. Read that rather than its implementation.

## The student kit

`student_kit/` is Samsung's provided material, committed unmodified:

| File | What it is |
|---|---|
| `schema.py` | The Pydantic response schema our output is validated against |
| `siis_responses.json` | 20 sample queries with their SIIS knowledge-base payloads |
| `deeplinks.json` | 578 Galaxy Settings deeplinks (URIs are masked placeholders) |
| `sample_output.json` | One fully worked example response |
| `input.txt` | The same 20 queries as plain text |

### On the deeplinks

The URIs in `deeplinks.json` are **masked placeholders** provided by Samsung
(`bixby://masked/act/...`). We match a step to an entry on the semantic meaning of its
`description`, `message` and `qna_description` fields, then copy the masked URI
**verbatim**. We never construct, guess, or generate a URI — the language model emits a
catalog id or `null`, never a link.

Two things worth knowing before trusting the catalog: its `message` field is often
misleading (`DL-0022` reads "View Reset Options" but means auto-reset after failed
unlock attempts), and 111 settings exist as both an Enable and a Disable entry with
identical descriptions. Both are documented in `docs/KIT_NOTES.md`.

## Who owns what

| | Area |
|---|---|
| **M1** | Deeplink matcher, SIIS → goal/action/step extraction pipeline, AI usage disclosure |
| **M2** | Strict validator, sanitizer, catalog→Deeplink projection, cache, latency work, Dockerfile |
| **M3** | FastAPI service, deployment and public URL, gate-scoring test harness, `results.jsonl` |
| **M4** | Query paraphrases, unseen test payloads, demo page, demo video, deck |

Nobody edits anyone else's area. If you need to, say so rather than editing.
