"""FastAPI service entrypoint. Owner: M3 (Claude.md section 8).

Run from the repo root: python -m uvicorn app.main:app --reload
"""
from pydantic import BaseModel

from fastapi import FastAPI
from student_kit.schema import ContextDeeplinkResponse

app = FastAPI(title="Bixel")


class SiisResponsePayload(BaseModel):
    title: str
    content: str


class TroubleshootRequest(BaseModel):
    query: str
    siis_response: SiisResponsePayload


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/troubleshoot", response_model=ContextDeeplinkResponse)
def troubleshoot(request: TroubleshootRequest) -> ContextDeeplinkResponse:
    """Scaffold wiring only — ignores the request body and always returns the
    same placeholder. The real path (extract -> to_deeplink_pair per step ->
    sanitize -> validate, wrapped in the cache) lands once M1/M2 hand off
    their functions (Claude.md Day 2, section 10)."""
    return _PLACEHOLDER_RESPONSE


_PLACEHOLDER_RESPONSE = ContextDeeplinkResponse.model_validate(
    {
        "contexts": [
            {
                "goal": "Follow these steps to perform this Scaffold Troubleshooting.",
                "title": "Scaffold Check",
                "score": 0.5,
                "actions": [
                    {
                        "actionName": "Confirm wiring",
                        "description": "It will confirm the service is wired",
                        "category": "manual",
                        "stepGroups": [
                            {
                                "steps": [
                                    "This is placeholder output from the scaffold, not real logic."
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
)
