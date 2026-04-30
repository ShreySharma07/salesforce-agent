"""
Mock LLM client. Returns canned responses keyed by `purpose`. Lets us
build everything around the LLM without making real API calls.

When you swap to the real Gemini client (after billing arrives), no other
code changes — same interface, same response shape.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.llm.client import LLMClient, LLMResponse, UsageEvent


# ---------------------------------------------------------------------------
# Canned responses keyed by purpose. Add more as we build features.
# ---------------------------------------------------------------------------

_CANNED_TEXT_RESPONSES: dict[str, str] = {
    "keyframe_understanding": (
        "User is in Salesforce Lightning. The Leads tab is open. They click "
        "the 'New' button in the top-right and a modal appears with fields "
        "for Name, Company, Email, Phone."
    ),
    "plan_synthesis": json.dumps({
        "goal": "Create a new lead in Salesforce from inbound email inquiries",
        "summary": "Reads new lead inquiry emails, extracts contact details, creates a Lead record in Salesforce with appropriate field mapping.",
        "steps": [
            {
                "id": "step_001",
                "kind": "navigate",
                "description": "Open Salesforce",
                "details": {"url": "https://login.salesforce.com", "expected_title_contains": "Salesforce"},
                "on_failure": "abort",
            },
            {
                "id": "step_002",
                "kind": "ui_action",
                "description": "Navigate to the Leads tab",
                "details": {
                    "intent": "Click the Leads tab in the navigation",
                    "target_description": "the Leads tab in the top navigation bar",
                },
                "on_failure": "pause",
            },
            {
                "id": "step_003",
                "kind": "ui_action",
                "description": "Open the New Lead form",
                "details": {
                    "intent": "Click the New button to open the lead creation modal",
                    "target_description": "the New button in the top-right of the Leads list",
                },
                "on_failure": "pause",
            },
        ],
        "decision_rules": [
            {
                "rule": "Lead Source should be set to 'Web' for inquiries from the contact form",
                "inferred_from": "User selected 'Web' in the Lead Source dropdown for both example leads",
            }
        ],
        "required_credentials": [
            {"name": "salesforce", "scopes": ["lead.read", "lead.write"]}
        ],
    }),
    "plan_correction": json.dumps({
        "goal": "Create a new lead in Salesforce from inbound email inquiries (corrected)",
        "summary": "Same as before, with user-requested change applied.",
        "steps": [],
        "decision_rules": [],
        "required_credentials": [{"name": "salesforce", "scopes": ["lead.read", "lead.write"]}],
    }),
}


class MockLLMClient(LLMClient):
    """Returns canned responses. For dev only."""

    provider_name = "mock"

    def __init__(self, model: str = "mock-model") -> None:
        self.model = model
        self._call_count = 0

    def generate(
        self,
        prompt: str,
        *,
        purpose: str,
        system: str | None = None,
        images: list[bytes] | None = None,
        json_mode: bool = False,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self._call_count += 1
        text = _CANNED_TEXT_RESPONSES.get(
            purpose,
            f"[MOCK RESPONSE for purpose={purpose!r}] no canned response defined",
        )
        usage = UsageEvent(
            model=self.model,
            provider=self.provider_name,
            input_tokens=len(prompt) // 4,                      # rough proxy
            output_tokens=len(text) // 4,
            cost_usd=0.0,
            latency_ms=10,
            purpose=purpose,
        )
        return LLMResponse(text=text, raw={"mock": True}, usage=usage)

    def generate_structured(
        self,
        prompt: str,
        *,
        purpose: str,
        schema: type,
        system: str | None = None,
        images: list[bytes] | None = None,
    ) -> tuple[Any, UsageEvent]:
        response = self.generate(prompt, purpose=purpose, system=system, images=images, json_mode=True)
        try:
            parsed = json.loads(response.text)
        except json.JSONDecodeError:
            parsed = {}
        instance = schema(**parsed) if hasattr(schema, "__init__") else parsed
        return instance, response.usage