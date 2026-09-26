"""Response validator. Owner: M2 (docs/PLAN.md, "Who owns what").

validate(response) -> {"ok": bool, "errors": [str]}

Checks every formatting rule in docs/KIT_NOTES.md section 8 ("The formatting rules, in
one place") that schema.py itself does not enforce: goal regex + trailing period, title
is 2-3 words, every action description is 5-7 words and starts with "It will", score in
[0.0, 1.0], every steps list non-empty, category is present (not silently defaulted),
every "auto" action carries an actionableDeeplink, and a URL/URI scan against the two
catalog allowlists (act URIs vs val URIs -- zero overlap between them).

Why this exists at all: **schema.py contains no validators.** It is plain Pydantic, so
``{"contexts": []}`` is "schema-valid", and so is ``score: 5.0``. Gate G4 (>=90%
schema-valid) is therefore nearly free, while every formatting rule is scored separately
under A1 by the grader's own checks. Passing schema.py proves almost nothing.

First acceptance test: this must REJECT student_kit/sample_output.json -- its two
Action.description values are 9 and 12 words, over the 5-7 word limit, and its goal has
no trailing period. If validate() passes that file, validate() is wrong.

Every error is collected with a path like ``contexts[0].actions[1].description`` rather
than raising on the first one, so a caller can see everything wrong in one pass.
"""
import re
from typing import Any

from backend.matcher.catalog import (
    DUMMY_DEEPLINK,
    actionable_uris,
    contains_url,
    iter_url_matches,
    load_catalog,
    validation_uris,
)

#: The goal string, with the trailing period. Decided by the team; the kit's own
#: sample_output.json omits it, which is one of the reasons that file must fail.
GOAL_PATTERN = re.compile(
    r"^Follow these steps to perform this .+ (?:Troubleshooting|Configuration)\.$"
)

TITLE_MIN_WORDS, TITLE_MAX_WORDS = 2, 3
DESCRIPTION_MIN_WORDS, DESCRIPTION_MAX_WORDS = 5, 7
DESCRIPTION_PREFIX = "It will"
VALID_CATEGORIES = frozenset({"auto", "manual", "critical"})

_catalog_cache: dict[str, frozenset[str]] | None = None


def _allowlists() -> dict[str, frozenset[str]]:
    """The two URI allowlists, loaded once.

    Separate namespaces with zero overlap: 578 ``masked/act`` URIs (plus the single
    ``bixby://dummy_positive``) and 419 ``masked/val`` URIs. A single combined list would
    let a validation URI be accepted as an actionable one.
    """
    global _catalog_cache
    if _catalog_cache is None:
        entries = load_catalog()
        _catalog_cache = {
            "actionable": actionable_uris(entries),
            "validation": validation_uris(entries),
        }
    return _catalog_cache


def _word_count(text: str) -> int:
    return len(text.split())


def validate(response: Any) -> dict[str, Any]:
    """Check a response dict against every rule the grader applies.

    Accepts a plain dict (what the API serialises) rather than a Pydantic model, because
    two of the rules -- "category is present" and "contexts is non-empty" -- cannot be
    checked after Pydantic has filled in its defaults.
    """
    errors: list[str] = []

    if not isinstance(response, dict):
        return {"ok": False, "errors": [f"response must be a dict, got {type(response).__name__}"]}

    contexts = response.get("contexts")
    if contexts is None:
        errors.append("response has no 'contexts' key")
        return {"ok": False, "errors": errors}
    if not isinstance(contexts, list):
        errors.append(f"contexts must be a list, got {type(contexts).__name__}")
        return {"ok": False, "errors": errors}
    if not contexts:
        # Schema-valid but banned by decision: it passes G4 and G5 while scoring zero on
        # A4 generalization. See docs/KIT_NOTES.md section 1.
        errors.append("contexts is empty -- banned: schema-valid but scores zero on A4")

    for goal_index, goal in enumerate(contexts):
        errors.extend(_check_goal(goal, f"contexts[{goal_index}]"))

    errors.extend(_check_urls(response))

    return {"ok": not errors, "errors": errors}


def _check_goal(goal: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(goal, dict):
        return [f"{path} must be an object, got {type(goal).__name__}"]

    goal_text = goal.get("goal")
    if not isinstance(goal_text, str) or not goal_text.strip():
        errors.append(f"{path}.goal is missing or empty")
    elif not GOAL_PATTERN.match(goal_text):
        errors.append(
            f"{path}.goal does not match the required pattern "
            f'"Follow these steps to perform this <Name> Troubleshooting." '
            f"(note the trailing period): {goal_text!r}"
        )

    title = goal.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{path}.title is missing or empty")
    else:
        words = _word_count(title)
        if not TITLE_MIN_WORDS <= words <= TITLE_MAX_WORDS:
            errors.append(
                f"{path}.title must be {TITLE_MIN_WORDS}-{TITLE_MAX_WORDS} words, "
                f"got {words}: {title!r}"
            )

    score = goal.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        errors.append(f"{path}.score must be a number, got {type(score).__name__}")
    elif not 0.0 <= float(score) <= 1.0:
        errors.append(f"{path}.score must be within [0.0, 1.0], got {score}")

    actions = goal.get("actions")
    if not isinstance(actions, list) or not actions:
        errors.append(f"{path}.actions is missing or empty")
    else:
        for action_index, action in enumerate(actions):
            errors.extend(_check_action(action, f"{path}.actions[{action_index}]"))
    return errors


def _check_action(action: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(action, dict):
        return [f"{path} must be an object, got {type(action).__name__}"]

    name = action.get("actionName")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{path}.actionName is missing or empty")

    description = action.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{path}.description is missing or empty")
    else:
        if not description.startswith(DESCRIPTION_PREFIX):
            errors.append(
                f'{path}.description must start with "{DESCRIPTION_PREFIX}": {description!r}'
            )
        words = _word_count(description)
        if not DESCRIPTION_MIN_WORDS <= words <= DESCRIPTION_MAX_WORDS:
            errors.append(
                f"{path}.description must be {DESCRIPTION_MIN_WORDS}-"
                f"{DESCRIPTION_MAX_WORDS} words, got {words}: {description!r}"
            )

    # schema.py makes category Optional with a silent default of "manual", so a dropped
    # field does not raise -- it quietly downgrades an auto action. Require it explicitly.
    if "category" not in action:
        errors.append(
            f"{path}.category is absent -- schema.py would silently default it to 'manual'"
        )
        category = None
    else:
        category = action.get("category")
        if category not in VALID_CATEGORIES:
            errors.append(
                f"{path}.category must be one of {sorted(VALID_CATEGORIES)}, got {category!r}"
            )

    step_groups = action.get("stepGroups")
    if not isinstance(step_groups, list) or not step_groups:
        errors.append(f"{path}.stepGroups is missing or empty")
        return errors

    has_actionable = False
    for group_index, group in enumerate(step_groups):
        group_path = f"{path}.stepGroups[{group_index}]"
        group_errors, group_has_actionable = _check_step_group(group, group_path)
        errors.extend(group_errors)
        has_actionable = has_actionable or group_has_actionable

    # An "auto" action with no actionableDeeplink is an automatic A2 deduction.
    if category == "auto" and not has_actionable:
        errors.append(
            f"{path} has category 'auto' but no stepGroup carries an actionableDeeplink"
        )
    return errors


def _check_step_group(group: Any, path: str) -> tuple[list[str], bool]:
    errors: list[str] = []
    if not isinstance(group, dict):
        return [f"{path} must be an object, got {type(group).__name__}"], False

    steps = group.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append(f"{path}.steps must be a non-empty list")
    else:
        for step_index, step in enumerate(steps):
            if not isinstance(step, str) or not step.strip():
                errors.append(f"{path}.steps[{step_index}] is empty or not a string")

    allowlists = _allowlists()

    actionable = group.get("actionableDeeplink")
    has_actionable = False
    if actionable is not None:
        if not isinstance(actionable, dict):
            errors.append(f"{path}.actionableDeeplink must be an object or null")
        else:
            has_actionable = True
            uri = actionable.get("deeplink")
            if not isinstance(uri, str) or not uri:
                errors.append(f"{path}.actionableDeeplink.deeplink is missing")
            elif uri not in allowlists["actionable"]:
                errors.append(
                    f"{path}.actionableDeeplink.deeplink is not in the catalog: {uri!r}"
                )
            if not (actionable.get("description") or "").strip():
                errors.append(f"{path}.actionableDeeplink.description is missing or empty")
            if uri == DUMMY_DEEPLINK:
                errors.extend(_check_dummy_text(actionable, path))

    validation = group.get("validationDeeplink")
    if validation is not None:
        if not isinstance(validation, dict):
            errors.append(f"{path}.validationDeeplink must be an object or null")
        else:
            uri = validation.get("deeplink")
            if not isinstance(uri, str) or not uri:
                errors.append(f"{path}.validationDeeplink.deeplink is missing")
            elif uri not in allowlists["validation"]:
                errors.append(
                    f"{path}.validationDeeplink.deeplink is not a known validation URI: {uri!r}"
                )
            if not (validation.get("key") or "").strip():
                errors.append(f"{path}.validationDeeplink.key is required and missing")

    return errors, has_actionable


def _check_dummy_text(actionable: dict[str, Any], path: str) -> list[str]:
    """The placeholder entry requires description and message we write ourselves.

    Its own qna_description says: "Write description and message yourself (5-7 words,
    naming the concrete screen from the steps)."
    """
    errors: list[str] = []
    for field in ("description", "message"):
        text = actionable.get(field) or ""
        words = _word_count(text)
        if not DESCRIPTION_MIN_WORDS <= words <= DESCRIPTION_MAX_WORDS:
            errors.append(
                f"{path}.actionableDeeplink.{field} for {DUMMY_DEEPLINK} must be "
                f"{DESCRIPTION_MIN_WORDS}-{DESCRIPTION_MAX_WORDS} words, got {words}: {text!r}"
            )
    return errors


def _check_urls(response: Any, path: str = "response") -> list[str]:
    """Gate G5: zero URLs anywhere. One leak zeroes the whole automated score.

    ``deeplink`` fields are skipped -- ``bixby://`` URIs are what we are supposed to
    emit, and they are separately checked against the allowlists above.
    """
    errors: list[str] = []
    if isinstance(response, str):
        if not path.endswith("deeplink") and contains_url(response):
            found = iter_url_matches(response)
            errors.append(f"URL-shaped text at {path}: {found} in {response[:80]!r}")
    elif isinstance(response, dict):
        for key, value in response.items():
            errors.extend(_check_urls(value, f"{path}.{key}"))
    elif isinstance(response, list):
        for index, item in enumerate(response):
            errors.extend(_check_urls(item, f"{path}[{index}]"))
    return errors
