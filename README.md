# Bixel

**Smart Guided Troubleshooting Engine** — Samsung PRISM GenAI Hackathon 2026–27, Theme 2 (Guided Troubleshooting).

Given a user's natural-language complaint about their Galaxy device, plus the
matching SIIS knowledge-base text, Bixel returns a structured, validated
troubleshooting plan in which every actionable step is linked to a real Galaxy
Settings deeplink taken from Samsung's provided catalog.

## Status — complete and deployed

**Live service: <https://bixel-mmvy.onrender.com>** — that URL serves the API *and* the
demo page. Open it in a browser and the system demonstrates itself; there is no separate
front end deployed anywhere.

Submission deadline **30 September 2026**. Everything below is built, tested and running in
production — there are no stubs or placeholders left in the request path.

| Piece | State |
|---|---|
| `GET /health` | **live** — returns exactly `{"status": "ok"}` |
| `POST /v1/troubleshoot` | **live** — full pipeline: `extract()` → catalog projection → sanitize → validate, wrapped in the cache |
| Deeplink matcher (`backend/matcher`) | **complete and measured** — precision 1.000, recall 0.800, abstention 1.000 |
| Validator, sanitizer, projection (`app/`) | **complete** — enforce every formatting rule `schema.py` does not |
| Cache (`app/cache.py`) | **complete and measured** — intent-embedding + content-hash keys, threshold 0.85 |
| Extraction pipeline (`backend/extract`) | **complete** — Gemini with a measured model chain, grounding checks, and a no-LLM fallback that is never empty |
| Query variations (`app/variations.py`) | **complete** — 8–10 paraphrases per query, committed to `fixtures/paraphrases.json` |
| Demo page (`app/static/index.html`) | **live at `/`** — served by the API itself |
| `results.jsonl` | **committed** — 20 rows, `query` byte-identical to the kit's `original_query` |
| Docker / `docker-compose.yml` | **working** — `docker compose up --build` brings the whole service up |

### Gate results, measured against the live deployment

Reproduce it yourself:

```bash
python -m scripts.run_harness --target https://bixel-mmvy.onrender.com
```

| Gate | Requirement | Result |
|---|---|---|
| **G2** | `GET /health` returns exactly `{"status": "ok"}` | **PASS** |
| **G3** | ≥95% of test queries present in `results.jsonl` | **PASS** — 20/20 = 100% |
| **G4** | ≥90% of responses schema-valid | **PASS** — 20/20 = 100% |
| **G5** | Zero URL leaks anywhere in the output | **PASS** — 0 leaks |

| Scored axis | Result |
|---|---|
| **A1** formatting | 20/20 responses pass every rule |
| **A2** deeplinks | 0 URIs outside the catalog allowlists; 0 `auto` actions missing a deeplink |
| **A4** generalization | 8/8 unseen payloads valid, non-empty, fully grounded, leak-free |
| **A5** variations | 162 paraphrases across 20 queries; 0 outside the 8–10 band; 0 duplicates |

`{"contexts": []}` is a banned code path, not merely an unlikely one — every error path,
including a Gemini outage, returns a real non-empty answer built from the supplied SIIS
text. The harness fails the run if it ever sees an empty one.

### A3 — measured latency and cache numbers

Measured by `python -m scripts.measure_cache_speed` against the real Gemini API and the
real 20 kit rows. These are measurements, not estimates.

| Number | Target | Measured |
|---|---|---|
| Repeat-query latency, p95 | ≤ 300 ms | **20.3 ms** |
| Cache hit rate on repeats | ≥ 90% | **100%** (20/20) |
| Paraphrase hit rate (threshold 0.85) | ≥ 80% | **86.4%** (140/162), with **zero** wrong-row hits |
| Cold start, p95 | ≤ 8 s | **4.45 s** |

Two honest caveats on that table:

- **The 4.45 s cold start is a local process start** — Python boot plus warming the ONNX
  embedding model. It is not the same thing as the deployed service waking up.
- **A cold wake on Render's free tier measured 33 s**, against 0.29–0.93 s once warm. That
  is the free tier spinning the container down after ~15 minutes of no traffic, not the
  application being slow. `.github/workflows/keep-alive.yml` pings `/health` every 5
  minutes so it never reaches that idle cut-off, and asserts the exact G2 body while it is
  there — so it doubles as a standing gate canary.

The cache threshold of 0.85 was chosen by measurement, not feel. The highest similarity
between two *distinct* kit queries sharing a document is 0.844, so 0.85 is the first value
above every real collision — giving 86.4% paraphrase recall with zero wrong-row hits. The
full sweep from 0.80 to 0.95 is reproducible with the script above.

### Known and deliberate

- **Deeplink coverage is low, and that is the correct behaviour.** Roughly 1–2 of ~36 step
  groups across the 20 kit rows carry a deeplink. The ceiling is 10 of 90 document
  sections: Samsung's catalog is entirely Settings toggles, while most SIIS documents
  instruct physical actions (inspect the cable, hold the power button) or support
  escalation, for which no Settings deeplink exists or should be invented. Lowering the
  matcher threshold to inflate this number would trade A2 validity for A2 coverage and
  lose. The harness prints coverage against that ceiling on every run, so it cannot drift
  unnoticed.
- **Which step groups receive a deeplink varies slightly between runs** on byte-identical
  input, despite `temperature=0`. It is documented in
  [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) so the demo never depends on a specific
  deeplink appearing on cue.

See [`docs/PLAN.md`](docs/PLAN.md) for who built what and when,
[`docs/KIT_NOTES.md`](docs/KIT_NOTES.md) for the kit's traps — read that one before
writing any code against `student_kit/` — and
[`AI_USAGE_DISCLOSURE.md`](AI_USAGE_DISCLOSURE.md) for exactly what AI produced here.

## API

Base URL: **`https://bixel-mmvy.onrender.com`**

| Endpoint | Behaviour |
|---|---|
| `POST /v1/troubleshoot` | Takes `{query, siis_response: {title, content}}`, returns a `ContextDeeplinkResponse` |
| `GET /health` | Returns exactly `{"status": "ok"}` |
| `GET /` | The demo page, served by the API itself |
| `GET /metrics` | Cache stats, request count, latency percentiles, model chain, build marker |
| `GET /demo/scenarios` | The kit rows the demo page's dropdown is built from |

**No authentication on any endpoint.** Judges call them directly with no key
and no headers, so no auth middleware goes into this service.

Copy-pasteable, against the live service:

```bash
curl https://bixel-mmvy.onrender.com/health
# {"status":"ok"}

curl -X POST https://bixel-mmvy.onrender.com/v1/troubleshoot \
  -H 'Content-Type: application/json' \
  -d '{"query":"My Galaxy S22 screen inputs are delayed and the touch responsiveness is laggy.","siis_response":{"title":"Screen is slow to respond to touch","content":"## Adjust touch sensitivity\nTurn on Touch sensitivity to improve screen response when using a screen protector."}}'
```

The first call after a quiet period may take up to ~30 s while the free-tier container
wakes; every call after that is sub-second. See the A3 caveats above.

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

Then open <http://127.0.0.1:8000/> for the demo page.

### Run with Docker

One command from a clean clone, which is how a judge should reach it:

```bash
cp .env.example .env        # then put a real GEMINI_API_KEY in it
docker compose up --build
# then open http://localhost:8000/
```

### Test

```bash
python -m pytest -q                              # whole suite
python -m scripts.run_harness --target local     # score every gate, in-process
python -m scripts.measure_cache_speed            # the A3 latency/cache numbers
python -m backend.matcher.evaluate               # matcher threshold sweep + scores
python -m scripts.measure_matcher_precision      # sentence-level dataset + rejected baseline
```

The default suite needs no API key and no network — the Gemini path is faked. The 28 tests
that call the real API are opt-in with `BIXEL_LIVE_LLM=1` and need `GEMINI_API_KEY` set.

## Layout

```
app/                  FastAPI service and the pipeline it assembles
  main.py             /health, /v1/troubleshoot, /, /metrics, /demo/scenarios  (M3)
  validator.py        formatting rules schema.py does not enforce (M2)
  sanitizer.py        strips URL-shaped tokens from output     (M2)
  projection.py       catalog entry -> Deeplink, verbatim      (M2)
  cache.py            intent + content-hash response cache     (M2)
  extractor.py        SIIS text -> goals/actions/steps via LLM (M1)
  variations.py       8-10 query paraphrases                   (M4)
  static/index.html   the demo page, served at /               (M4)
  matcher_labels.json 29 sentence-level matcher labels
backend/matcher/      deeplink matcher, complete               (M1)
backend/extract/      Gemini pipeline, grounding, fallbacks    (M1)
scripts/              gate harness and the measurement scripts (M3/M2)
student_kit/          Samsung's provided material, unmodified
testdata/             8 invented SIIS payloads, for A4          (M4)
docs/                 PLAN.md, KIT_NOTES.md, DEMO_SCRIPT.md
fixtures/             golden responses + the paraphrase file   (M2/M4)
tests/                service, pipeline and demo-endpoint tests
.github/workflows/    keep-alive ping, doubling as a G2 canary
results.jsonl         the graded submission artefact           (M3)
AI_USAGE_DISCLOSURE.md  what AI produced, specifically         (M1)
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
