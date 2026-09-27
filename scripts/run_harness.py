"""Gate-scoring harness. Owner: M3 (docs/PLAN.md, Day 2 checkpoint).

    python -m scripts.run_harness --target local
    python -m scripts.run_harness --target https://bixel-mmvy.onrender.com --write

Does two jobs:

1. **Writes ``results.jsonl``**, the submission artefact: one object per kit query, shaped
   ``{"query", "query_variations", "response"}``.
2. **Scores us against the gates before we submit**, rather than finding out afterwards.

Why this is the most important script in the repo: **gate G3 is "at least 95% of test
queries appear in our results file"**, and any single gate failing zeroes the entire
automated score -- all 60 points, no partial credit. Until this produced a results file, a
perfect pipeline would still have scored nothing.

It reimplements no rule. Every check delegates to the code that already owns it:

* ``app/validator.py: validate()``        -- all A1 formatting rules, allowlists, URL scan
* ``backend/matcher/catalog.py``          -- the two URI allowlists and the URL detector
* ``student_kit/schema.py``               -- G4, checked against the grader's own file
* ``backend/extract/grounding.py``        -- A4, whether steps trace to the source text
* ``app/variations.py: variations()``     -- A5, only if the fixture lacks a row

Exits non-zero if any gate fails, so it is usable as a pre-submit check and in CI.

It also states plainly what it does **not** score. A3 (cache and latency) is measured by
``scripts/measure_cache_speed.py``; nothing here invents a number for it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.validator import validate
from backend.extract.grounding import is_grounded
from backend.matcher.catalog import (
    actionable_uris,
    contains_url,
    load_catalog,
    validation_uris,
)
from student_kit.schema import ContextDeeplinkResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT_PATH = REPO_ROOT / "student_kit" / "siis_responses.json"
PARAPHRASES_PATH = REPO_ROOT / "fixtures" / "paraphrases.json"
UNSEEN_PATH = REPO_ROOT / "testdata" / "unseen_siis.json"
RESULTS_PATH = REPO_ROOT / "results.jsonl"

#: G3's bar. Gates are pass/fail with no partial credit.
MIN_QUERY_COVERAGE = 0.95
#: G4's bar.
MIN_SCHEMA_VALIDITY = 0.90
#: A5's range. Not 7, not 11.
MIN_VARIATIONS, MAX_VARIATIONS = 8, 10

DEPLOYED_URL = "https://bixel-mmvy.onrender.com"


# --------------------------------------------------------------------- transports


def local_transport() -> tuple[Callable[[dict], dict], Callable[[], dict]]:
    """Drive the API in-process. No network, no deployment needed.

    Used for iterating on the scorecard: an uncached request costs ~7s against the live
    service, and waiting three minutes to find a formatting bug in this script is wasteful.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    client.__enter__()  # run the lifespan so the model is warmed, as in production

    def post(payload: dict) -> dict:
        response = client.post("/v1/troubleshoot", json=payload)
        response.raise_for_status()
        return response.json()

    def health() -> dict:
        return client.get("/health").json()

    return post, health


def http_transport(base_url: str) -> tuple[Callable[[dict], dict], Callable[[], dict]]:
    """Drive the deployed service over HTTP. This is what judges actually score."""
    import httpx

    base = base_url.rstrip("/")
    # Generous timeout: an uncached request pays a real Gemini call, and a free-tier
    # instance may additionally be waking from sleep.
    client = httpx.Client(timeout=180.0)

    def post(payload: dict) -> dict:
        response = client.post(f"{base}/v1/troubleshoot", json=payload)
        response.raise_for_status()
        return response.json()

    def health() -> dict:
        return client.get(f"{base}/health").json()

    return post, health


# ------------------------------------------------------------------------- data


def load_kit_rows() -> list[dict]:
    payload = json.loads(KIT_PATH.read_text(encoding="utf-8"))
    return payload["responses"]


def load_variations(rows: list[dict]) -> dict[str, list[str]]:
    """Paraphrases per row id, from the committed fixture.

    The fixture is real Gemini output, already verified at 8-9 per row and unique. Reusing
    it keeps ``results.jsonl`` reproducible with no API call, which matters because three of
    six Gemini models were returning 503 when the model chain was measured -- a 503 partway
    through generation would leave the submission half-built.
    """
    out: dict[str, list[str]] = {}
    if PARAPHRASES_PATH.exists():
        fixture = json.loads(PARAPHRASES_PATH.read_text(encoding="utf-8"))
        out = {k: v for k, v in fixture.items() if k != "_readme" and isinstance(v, list)}

    missing = [row["id"] for row in rows if row["id"] not in out]
    if missing:
        print(f"  fixture missing {len(missing)} row(s); generating those live: {missing}")
        from app.variations import variations

        for row in rows:
            if row["id"] in missing:
                out[row["id"]] = variations(row["original_query"])
    return out


# ------------------------------------------------------------------------ report


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str


@dataclass
class Report:
    gates: list[GateResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def gate(self, name: str, passed: bool, detail: str) -> None:
        self.gates.append(GateResult(name, passed, detail))

    @property
    def all_gates_passed(self) -> bool:
        return all(gate.passed for gate in self.gates)


# ------------------------------------------------------------------------ checks


def _walk_strings(payload: Any, path: str = "response"):
    if isinstance(payload, str):
        yield path, payload
    elif isinstance(payload, dict):
        for key, value in payload.items():
            yield from _walk_strings(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            yield from _walk_strings(item, f"{path}[{index}]")


def check_url_leaks(records: list[dict]) -> list[str]:
    """G5. ``deeplink`` fields are exempt -- ``bixby://`` URIs are what we must emit."""
    offenders: list[str] = []
    for record in records:
        for path, value in _walk_strings(record["response"]):
            if path.endswith("deeplink"):
                continue
            if contains_url(value):
                offenders.append(f"{record['query'][:40]!r} at {path}: {value[:60]!r}")
    return offenders


def check_schema_validity(records: list[dict]) -> tuple[int, list[str]]:
    """G4, against the grader's own schema.py rather than our own opinion of it."""
    valid = 0
    failures: list[str] = []
    for record in records:
        try:
            ContextDeeplinkResponse.model_validate(record["response"])
            valid += 1
        except Exception as exc:  # noqa: BLE001 - any rejection is a failure
            failures.append(f"{record['query'][:40]!r}: {type(exc).__name__}")
    return valid, failures


def count_deeplink_opportunity(rows: list[dict]) -> dict[str, int]:
    """How many sections the matcher could offer a candidate for at all.

    Without this, A2 coverage is uninterpretable. Coverage of 5% reads like a bug until you
    know the ceiling: the catalog is a Settings-toggle catalog, while these documents mostly
    instruct physical actions, app navigation and support escalation. There is no entry for
    safe mode, clearing cache, Smart Switch, screen mirroring or factory reset, so most
    sections *correctly* have nothing to offer. Reporting coverage against opportunity
    rather than against every step group is the difference between a number that means
    something and one that invites the wrong fix -- lowering the matcher threshold to
    manufacture coverage, which trades A2 validity for A2 coverage and loses.
    """
    from backend.extract.candidates import build_candidates

    sections = with_candidates = options = rows_with = 0
    for row in rows:
        built = build_candidates(row["siis_response"])
        sections += len(built)
        section_hits = sum(1 for section in built if section.options)
        with_candidates += section_hits
        options += sum(len(section.options) for section in built)
        rows_with += 1 if section_hits else 0
    return {
        "sections": sections,
        "sections_with_candidates": with_candidates,
        "options": options,
        "rows_with_candidates": rows_with,
        "rows": len(rows),
    }


def check_deeplinks(records: list[dict]) -> dict[str, Any]:
    """A2. Every emitted URI must be in the correct allowlist -- two namespaces, not one."""
    entries = load_catalog()
    allowed_act, allowed_val = actionable_uris(entries), validation_uris(entries)

    total_groups = with_deeplink = 0
    invalid: list[str] = []
    auto_without: list[str] = []

    for record in records:
        for goal in record["response"].get("contexts") or []:
            for action in goal.get("actions") or []:
                has_any = False
                for group in action.get("stepGroups") or []:
                    total_groups += 1
                    actionable = group.get("actionableDeeplink")
                    if actionable:
                        has_any = True
                        with_deeplink += 1
                        if actionable.get("deeplink") not in allowed_act:
                            invalid.append(f"act {actionable.get('deeplink')}")
                    validation = group.get("validationDeeplink")
                    if validation and validation.get("deeplink") not in allowed_val:
                        invalid.append(f"val {validation.get('deeplink')}")
                if action.get("category") == "auto" and not has_any:
                    auto_without.append(action.get("actionName", "?"))

    return {
        "total_groups": total_groups,
        "with_deeplink": with_deeplink,
        "invalid": invalid,
        "auto_without_deeplink": auto_without,
    }


def check_variations(records: list[dict]) -> dict[str, Any]:
    """A5. The count itself is scored: not 7, not 11."""
    out_of_range: list[str] = []
    duplicated: list[str] = []
    for record in records:
        variants = record["query_variations"]
        if not MIN_VARIATIONS <= len(variants) <= MAX_VARIATIONS:
            out_of_range.append(f"{record['query'][:36]!r} has {len(variants)}")
        if len(set(variants)) != len(variants):
            duplicated.append(record["query"][:36])
    return {"out_of_range": out_of_range, "duplicated": duplicated}


def check_formatting(records: list[dict]) -> dict[str, Any]:
    """A1, delegated wholly to validate() so there is one definition of every rule."""
    clean = 0
    errors: list[str] = []
    for record in records:
        result = validate(record["response"])
        if result["ok"]:
            clean += 1
        else:
            for error in result["errors"][:3]:
                errors.append(f"{record['query'][:36]!r}: {error}")
    return {"clean": clean, "errors": errors}


def check_generalization(post: Callable[[dict], dict]) -> dict[str, Any]:
    """A4, against documents the pipeline has never seen.

    The kit rows cannot measure this: the prompt and the grounding threshold were both
    developed against them. The failure this looks for is the model answering from its own
    knowledge of Samsung phones instead of from the supplied text -- a confident,
    well-formatted, ungrounded answer.
    """
    if not UNSEEN_PATH.exists():
        return {"skipped": "testdata/unseen_siis.json not present"}

    payloads = json.loads(UNSEEN_PATH.read_text(encoding="utf-8"))["payloads"]
    rows: list[dict[str, Any]] = []
    for item in payloads:
        siis = item["siis_response"]
        response = post({"query": item["query"], "siis_response": siis})
        result = validate(response)

        ungrounded: list[str] = []
        steps = 0
        for goal in response.get("contexts") or []:
            for action in goal.get("actions") or []:
                for group in action.get("stepGroups") or []:
                    for step in group.get("steps") or []:
                        steps += 1
                        if not is_grounded(step, siis["content"]):
                            ungrounded.append(step[:70])

        rows.append(
            {
                "id": item["id"],
                "valid": result["ok"],
                "errors": result["errors"][:2],
                "non_empty": bool(response.get("contexts")),
                "steps": steps,
                "ungrounded": ungrounded,
                "leak": any(
                    contains_url(v)
                    for p, v in _walk_strings(response)
                    if not p.endswith("deeplink")
                ),
            }
        )
    return {"rows": rows}


# -------------------------------------------------------------------------- run


def build_records(rows: list[dict], post: Callable[[dict], dict]) -> list[dict]:
    variations_by_row = load_variations(rows)
    records: list[dict] = []
    for index, row in enumerate(rows, start=1):
        siis = row["siis_response"]
        # `query` is original_query character for character. It is the machine-readable
        # field G3 most plausibly keys on, and it differs from input.txt on rows 1 and 17.
        query = row["original_query"]
        print(f"  [{index:2}/{len(rows)}] {row['id']:8} {query[:58]!r}")
        response = post({"query": query, "siis_response": siis})
        records.append(
            {
                "query": query,
                "query_variations": variations_by_row.get(row["id"], []),
                "response": response,
            }
        )
    return records


def write_results(records: list[dict], path: Path = RESULTS_PATH) -> None:
    """One JSON object per line, UTF-8. ``ensure_ascii=False`` keeps row_11's em dash
    readable rather than escaped; both forms decode identically."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def score(records: list[dict], rows: list[dict], health: dict, generalization: dict) -> Report:
    report = Report()

    # ---- G2
    report.gate(
        "G2  /health returns exactly {'status': 'ok'}",
        health == {"status": "ok"},
        repr(health),
    )

    # ---- G3
    expected = {row["original_query"] for row in rows}
    present = {record["query"] for record in records}
    coverage = len(expected & present) / len(expected) if expected else 0.0
    report.gate(
        f"G3  >={MIN_QUERY_COVERAGE:.0%} of test queries present in results.jsonl",
        coverage >= MIN_QUERY_COVERAGE,
        f"{len(expected & present)}/{len(expected)} = {coverage:.1%}"
        + (f"  missing: {sorted(expected - present)[:2]}" if expected - present else ""),
    )

    # ---- G4
    valid, schema_failures = check_schema_validity(records)
    ratio = valid / len(records) if records else 0.0
    report.gate(
        f"G4  >={MIN_SCHEMA_VALIDITY:.0%} of responses schema-valid",
        ratio >= MIN_SCHEMA_VALIDITY,
        f"{valid}/{len(records)} = {ratio:.1%}"
        + (f"  {schema_failures[:2]}" if schema_failures else ""),
    )

    # ---- G5
    leaks = check_url_leaks(records)
    report.gate("G5  zero URL leaks anywhere", not leaks, f"{len(leaks)} leak(s)" + (f" {leaks[:2]}" if leaks else ""))

    # ---- banned case
    empty = [r["query"][:36] for r in records if not r["response"].get("contexts")]
    report.gate(
        "--  contexts non-empty on every row (banned by decision)",
        not empty,
        f"{len(empty)} empty" + (f" {empty[:2]}" if empty else ""),
    )

    return report


def print_report(
    report: Report, records: list[dict], generalization: dict, rows: list[dict]
) -> None:
    print("\n" + "=" * 74)
    print("MUST-PASS GATES  (missing any one zeroes the entire automated score)")
    print("=" * 74)
    for gate in report.gates:
        print(f"  [{'PASS' if gate.passed else 'FAIL'}] {gate.name}")
        print(f"         {gate.detail}")

    print("\n" + "-" * 74)
    print("SCORED AXES  (only counted if every gate above passes)")
    print("-" * 74)

    formatting = check_formatting(records)
    print(f"  A1 formatting : {formatting['clean']}/{len(records)} responses pass every rule")
    for error in formatting["errors"][:6]:
        print(f"                  - {error}")

    deeplinks = check_deeplinks(records)
    opportunity = count_deeplink_opportunity(rows)
    print(
        f"  A2 deeplinks  : {len(deeplinks['invalid'])} not in the catalog; "
        f"{len(deeplinks['auto_without_deeplink'])} auto action(s) missing one  "
        f"<- these two are what lose points"
    )
    print(
        f"                  coverage {deeplinks['with_deeplink']}/{deeplinks['total_groups']} step groups"
    )
    print(
        f"                  ceiling: the matcher could offer a candidate for only "
        f"{opportunity['sections_with_candidates']}/{opportunity['sections']} sections "
        f"across {opportunity['rows_with_candidates']}/{opportunity['rows']} rows"
    )
    print(
        "                  low coverage here is expected, not a bug: the catalog is Settings"
    )
    print(
        "                  toggles, these documents mostly instruct physical actions and"
    )
    print(
        "                  support escalation. Do NOT lower the matcher threshold to raise it."
    )
    if deeplinks["invalid"]:
        print(f"                  invalid: {deeplinks['invalid'][:3]}")

    variations_report = check_variations(records)
    total = sum(len(r["query_variations"]) for r in records)
    print(
        f"  A5 variations : {total} across {len(records)} queries; "
        f"{len(variations_report['out_of_range'])} outside {MIN_VARIATIONS}-{MAX_VARIATIONS}; "
        f"{len(variations_report['duplicated'])} with duplicates"
    )
    for item in variations_report["out_of_range"][:4]:
        print(f"                  - {item}")

    if "skipped" in generalization:
        print(f"  A4 unseen     : skipped ({generalization['skipped']})")
    else:
        rows = generalization["rows"]
        ok = sum(1 for r in rows if r["valid"] and r["non_empty"] and not r["ungrounded"] and not r["leak"])
        print(f"  A4 unseen     : {ok}/{len(rows)} payloads valid, non-empty, fully grounded, leak-free")
        for row in rows:
            flags = []
            if not row["valid"]:
                flags.append(f"INVALID {row['errors']}")
            if not row["non_empty"]:
                flags.append("EMPTY")
            if row["leak"]:
                flags.append("URL LEAK")
            if row["ungrounded"]:
                flags.append(f"{len(row['ungrounded'])} ungrounded step(s)")
            print(f"                  {row['id']:22} steps={row['steps']:2}  {'; '.join(flags) or 'ok'}")
            for step in row["ungrounded"][:2]:
                print(f"                      ungrounded: {step!r}")

    print("\n  A3 latency    : not measured here -- run scripts.measure_cache_speed")
    print("  Note          : the gate bars above are reconstructed from the brief, not an")
    print("                  official rubric. They are our own QA floor.")

    print("\n" + "=" * 74)
    print("RESULT: " + ("ALL GATES PASS" if report.all_gates_passed else "AT LEAST ONE GATE FAILS"))
    print("=" * 74)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        default="local",
        help=f"'local' for in-process, or a base URL (deployed: {DEPLOYED_URL})",
    )
    parser.add_argument(
        "--write", action="store_true", help=f"write {RESULTS_PATH.name} (the submission artefact)"
    )
    parser.add_argument("--skip-unseen", action="store_true", help="skip the A4 payloads")
    args = parser.parse_args()

    print(f"target: {args.target}")
    post, health_fn = (
        local_transport() if args.target == "local" else http_transport(args.target)
    )

    print("\nchecking /health ...")
    health = health_fn()
    print(f"  {health}")

    rows = load_kit_rows()
    print(f"\nrunning {len(rows)} kit queries ...")
    records = build_records(rows, post)

    generalization: dict[str, Any] = {"skipped": "--skip-unseen"}
    if not args.skip_unseen:
        print("\nrunning the unseen A4 payloads ...")
        generalization = check_generalization(post)

    report = score(records, rows, health, generalization)
    print_report(report, records, generalization, rows)

    if args.write:
        write_results(records)
        print(f"\nwrote {RESULTS_PATH} ({len(records)} lines)")
    else:
        print(f"\n(not written -- pass --write to produce {RESULTS_PATH.name})")

    return 0 if report.all_gates_passed else 1


if __name__ == "__main__":
    sys.exit(main())
