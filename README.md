# Bixel

**Smart Guided Troubleshooting Engine** — Samsung PRISM GenAI Hackathon 2026–27, Theme 2 (Guided Troubleshooting).

Given a user's natural-language complaint about their Galaxy device, plus the
matching SIIS knowledge-base text, Bixel returns a structured, validated
troubleshooting plan in which every actionable step is linked to a real Galaxy
Settings deeplink taken from Samsung's provided catalog.

## Status

Day 1 of a 5-day build. Submission deadline **30 September 2026**.

This repo currently contains the provided student kit and project scaffolding
only. No service code yet — see [`docs/PLAN.md`](docs/PLAN.md) for who is
building what, and when.

## API

| Endpoint | Behaviour |
|---|---|
| `POST /v1/troubleshoot` | Takes `{query, siis_response: {title, content}}`, returns a `ContextDeeplinkResponse` |
| `GET /health` | Returns exactly `{"status": "ok"}` |

**No authentication on either endpoint.** Judges call them directly with no key
and no headers, so no auth middleware goes into this service.

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
(`bixby://masked/act/...`). We match a step to an entry on the semantic meaning of
its `description`, `message` and `qna_description` fields, then copy the masked URI
**verbatim**. We never construct, guess, or generate a URI. The language model in
this pipeline emits a catalog id or `null` — never a link.

Read [`docs/KIT_NOTES.md`](docs/KIT_NOTES.md) before writing any code against the
kit. It documents several things the kit's own README does not, including three
rows whose "URL-free" text contains a URL.

## Setup

To be filled in once the service exists.

## Who owns what

| | Area |
|---|---|
| **M1** | Deeplink matcher, SIIS → goal/action/step extraction pipeline, AI usage disclosure |
| **M2** | Strict validator, sanitizer, catalog→Deeplink projection, cache, latency work, Dockerfile |
| **M3** | FastAPI service, deployment and public URL, gate-scoring test harness, `results.jsonl`, README |
| **M4** | Query paraphrases, unseen test payloads, demo page, demo video, deck |

Nobody edits anyone else's area. If you need to, say so rather than editing.
