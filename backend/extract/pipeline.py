"""``extract()`` -- the extraction pipeline.

    extract(query, siis_response) -> ContextDeeplinkResponse

Order of operations, and why each step is where it is:

1. **candidates** -- run the matcher over the document's sections *before* calling the
   model, so the model is offered numbered shortcuts and never a URI. A hallucinated
   deeplink is therefore impossible rather than caught later.
2. **generate** -- one Gemini call, JSON mode, every rule stated in the prompt.
3. **assemble** -- map the model's shortcut *numbers* back to catalog entries and project
   them with M2's ``to_deeplink_pair()``. Any URI-looking text the model produced anyway is
   discarded here, because nothing it wrote is ever copied into a deeplink field.
4. **ground** -- drop any step that cannot be traced to the supplied document. This is the
   check ``validate()`` cannot do, and the one A4's unseen payloads punish.
5. **sanitize** -- strip URL-shaped text (three kit rows contain an email address).
6. **validate** -- every A1 formatting rule. On failure, retry once with validate's own
   error messages fed back, then fall back.
7. **fallback** -- a deterministic plan built from the document's headings.

``contexts`` is never empty on any path. That is the one invariant worth stating twice:
an empty response is schema-valid, so it would pass G4 and G5 silently while scoring zero
on A4.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from app.projection import to_deeplink_pair
from app.sanitizer import sanitize_response
from app.validator import validate
from student_kit.schema import ContextDeeplinkResponse

from .candidates import SectionCandidates, build_candidates
from .client import LLMClient, LLMUnavailable
from .fallback import build_fallback, make_description, make_goal, make_title
from .grounding import ground_response
from .prompt import build_prompt

logger = logging.getLogger(__name__)

#: One retry. A second retry costs another 1-3 seconds for a model that has already failed
#: the rules twice; the deterministic fallback is the better use of that time.
MAX_ATTEMPTS = 2

#: Anything scheme-like. Used to scrub model output defensively -- see _strip_uris.
_URI_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S*", re.IGNORECASE)

_VALID_CATEGORIES = {"auto", "manual", "critical"}


@dataclass
class ExtractionTrace:
    """Why the result looks the way it does. Attached for logging, not for the response."""

    route: str = "unknown"
    attempts: int = 0
    model: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    grounding: str | None = None
    llm_error: str | None = None
    deeplinks_attached: int = 0


def extract(
    query: str,
    siis_response: dict[str, Any],
    *,
    client: LLMClient | None = None,
    matcher: Callable[..., Sequence[Any]] | None = None,
    trace: ExtractionTrace | None = None,
) -> ContextDeeplinkResponse:
    """Turn a complaint plus a SIIS document into a validated response.

    ``client`` and ``matcher`` are injectable so the default test suite runs offline with no
    key and no quota. ``trace`` is filled in if provided, for logging the route taken.
    """
    report = trace if trace is not None else ExtractionTrace()
    siis = siis_response if isinstance(siis_response, dict) else {}
    content = siis.get("content") or ""
    title = siis.get("title") or ""

    payload = _try_model_route(query, title, content, siis, client, matcher, report)

    if payload is None:
        report.route = report.route if report.route != "unknown" else "fallback"
        payload = sanitize_response(build_fallback(query, siis))
        result = validate(payload)
        if not result["ok"]:
            # The fallback is tested against all 20 kit rows and against broken input, so
            # this should be unreachable. If it ever fires, something is very wrong with the
            # input and a minimal honest answer still beats an empty one.
            logger.error("fallback failed validation: %s", result["errors"][:5])
            report.route = "minimal"
            payload = sanitize_response(_minimal_response(query, title))

    return ContextDeeplinkResponse.model_validate(payload)


def _try_model_route(
    query: str,
    title: str,
    content: str,
    siis: dict[str, Any],
    client: LLMClient | None,
    matcher: Callable[..., Sequence[Any]] | None,
    report: ExtractionTrace,
) -> dict[str, Any] | None:
    """The LLM path. Returns None to mean "fall back", never raises."""
    if not content.strip():
        report.route = "fallback:no-content"
        return None

    try:
        active_client = client if client is not None else _default_client()
    except LLMUnavailable as exc:
        report.route = "fallback:no-client"
        report.llm_error = str(exc)
        return None
    if active_client is None:
        report.route = "fallback:no-client"
        return None

    sections = build_candidates(siis, matcher=matcher)
    if not sections:
        report.route = "fallback:no-sections"
        return None

    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        report.attempts = attempt
        prompt = build_prompt(query, title, sections, previous_errors=errors)

        try:
            raw = active_client.generate_json(prompt)
        except Exception as exc:  # noqa: BLE001 - any client failure means fall back
            report.route = "fallback:llm-error"
            report.llm_error = f"{type(exc).__name__}: {exc}"
            return None

        report.model = getattr(active_client, "last_model", None)

        parsed = _parse_json(raw)
        if parsed is None:
            errors = ["Your answer was not valid JSON. Return a single JSON document only."]
            continue

        payload, attached = _assemble(parsed, sections, query, title)
        report.deeplinks_attached = attached

        grounded, grounding_report = ground_response(payload, content)
        report.grounding = grounding_report.summary()
        if grounding_report.everything_dropped or not grounded.get("contexts"):
            report.route = "fallback:ungrounded"
            return None

        cleaned = sanitize_response(grounded)
        result = validate(cleaned)
        if result["ok"]:
            report.route = "model"
            report.validation_errors = []
            return cleaned

        report.validation_errors = result["errors"]
        errors = result["errors"]

    report.route = "fallback:invalid-after-retry"
    return None


def _default_client() -> LLMClient | None:
    from .client import GeminiClient

    return GeminiClient()


def _parse_json(raw: str) -> dict[str, Any] | None:
    """Parse the model's answer, tolerating a code fence it was told not to use."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _strip_uris(text: Any) -> str:
    """Remove any scheme-like token the model produced despite being told not to.

    Belt and braces. The model is never shown a URI and never asked for one, so this should
    find nothing -- but a step that smuggled one through would reach the response body.
    """
    if not isinstance(text, str):
        return ""
    return " ".join(_URI_RE.sub(" ", text).split())


def _assemble(
    parsed: dict[str, Any],
    sections: Sequence[SectionCandidates],
    query: str,
    title: str,
) -> tuple[dict[str, Any], int]:
    """Turn the model's answer into the response schema.

    Deeplinks are resolved here by *index lookup only*. Nothing the model wrote is ever
    copied into a deeplink field, which is what makes an invented URI impossible rather
    than merely detectable.
    """
    by_number = {section.number: section for section in sections}
    naming_source = title or query or "Device issue"
    attached = 0

    goals_out: list[dict[str, Any]] = []
    for goal in parsed.get("goals") or []:
        if not isinstance(goal, dict):
            continue
        actions_out: list[dict[str, Any]] = []

        for action in goal.get("actions") or []:
            if not isinstance(action, dict):
                continue

            steps = [_strip_uris(step) for step in (action.get("steps") or [])]
            steps = [step for step in steps if step]
            if not steps:
                continue

            section = by_number.get(_as_int(action.get("section")))
            option = section.option_by_number(action.get("shortcut")) if section else None

            actionable = validation = None
            if option is not None:
                try:
                    actionable, validation = to_deeplink_pair(option.entry)
                    attached += 1
                except ValueError:
                    actionable = validation = None

            category = action.get("category")
            if category not in _VALID_CATEGORIES:
                category = "manual"
            # "auto" is only honest when a shortcut actually resolved. This is also the
            # rule the validator enforces, so getting it right here avoids a retry.
            if category == "auto" and actionable is None:
                category = "manual"
            if actionable is not None and category == "manual":
                category = "auto"

            actions_out.append(
                {
                    "actionName": _strip_uris(action.get("actionName")) or "Follow Guidance",
                    "description": _fit_description(action.get("description"), section),
                    "category": category,
                    "stepGroups": [
                        {
                            "steps": steps,
                            "actionableDeeplink": actionable.model_dump() if actionable else None,
                            "validationDeeplink": validation.model_dump() if validation else None,
                        }
                    ],
                }
            )

        if not actions_out:
            continue

        goals_out.append(
            {
                "goal": _fit_goal(goal.get("name"), naming_source),
                "title": _fit_title(goal.get("title"), naming_source),
                "score": _fit_score(goal.get("score")),
                "actions": actions_out,
            }
        )

    return {"contexts": goals_out}, attached


def _as_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return -1


def _fit_goal(name: Any, naming_source: str) -> str:
    """Build the goal sentence ourselves from the model's name.

    The model is asked for a *name*, not the whole sentence, so the regex-critical wrapper
    and its trailing period are never at the mercy of the model's formatting.
    """
    if isinstance(name, str) and name.strip():
        cleaned = " ".join(_strip_uris(name).split()[:3])
        cleaned = re.sub(r"\b(troubleshooting|configuration)\b\.?$", "", cleaned, flags=re.I).strip()
        if cleaned:
            return f"Follow these steps to perform this {cleaned} Troubleshooting."
    return make_goal(naming_source)


def _fit_title(title: Any, naming_source: str) -> str:
    if isinstance(title, str) and title.strip():
        words = _strip_uris(title).split()
        if 2 <= len(words) <= 3:
            return " ".join(words)
        if len(words) > 3:
            return " ".join(words[:3])
    return make_title(naming_source)


def _fit_description(description: Any, section: SectionCandidates | None) -> str:
    """Accept the model's description only if it already obeys the rule."""
    if isinstance(description, str) and description.strip():
        cleaned = " ".join(_strip_uris(description).split())
        if cleaned.startswith("It will") and 5 <= len(cleaned.split()) <= 7:
            return cleaned
    return make_description(section.heading if section else "device issue")


def _fit_score(score: Any) -> float:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return 0.5
    return min(max(value, 0.0), 1.0)


def _minimal_response(query: str, title: str) -> dict[str, Any]:
    """Last resort. Never reached in testing, but keeps the never-empty rule absolute."""
    naming_source = title or query or "Device issue"
    return {
        "contexts": [
            {
                "goal": make_goal(naming_source),
                "title": make_title(naming_source),
                "score": 0.2,
                "actions": [
                    {
                        "actionName": "Contact Samsung Support",
                        "description": "It will help you contact support",
                        "category": "manual",
                        "stepGroups": [
                            {
                                "steps": [
                                    "Contact Samsung Support to arrange further assistance."
                                ],
                                "actionableDeeplink": None,
                                "validationDeeplink": None,
                            }
                        ],
                    }
                ],
            }
        ]
    }
