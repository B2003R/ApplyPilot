"""Constrained LLM fallback — structured JSON only, never browser actions."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from applypilot.apply.form_engine.models import (
    FieldCategory,
    FormField,
    LLMFieldResolution,
    SENSITIVE_CATEGORIES,
)
from applypilot.apply.form_engine.policy import EXACT_ANSWER_CATEGORIES, FillPolicy

ALLOWED_ACTIONS = {
    "fill_text",
    "select_option",
    "choose_radio",
    "check",
    "draft_only",
    "skip",
    "manual_required",
}


class LLMFallbackError(ValueError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Find first { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        raise LLMFallbackError("no_json_object")
    return json.loads(text[start : end + 1])


def parse_llm_resolution(data: dict[str, Any]) -> LLMFieldResolution:
    required = [
        "field_id",
        "category",
        "confidence",
        "action_type",
        "reasoning_summary",
    ]
    for key in required:
        if key not in data:
            raise LLMFallbackError(f"missing_field:{key}")

    action = data["action_type"]
    if action not in ALLOWED_ACTIONS:
        raise LLMFallbackError(f"unsafe_action:{action}")

    try:
        category = FieldCategory(data["category"])
    except ValueError as e:
        raise LLMFallbackError(f"unknown_category:{data['category']}") from e

    confidence = float(data["confidence"])
    if confidence < 0 or confidence > 1:
        raise LLMFallbackError("confidence_out_of_range")

    return LLMFieldResolution(
        field_id=str(data["field_id"]),
        category=category,
        profile_path=data.get("profile_path"),
        confidence=confidence,
        action_type=action,
        proposed_value=data.get("proposed_value"),
        reasoning_summary=str(data["reasoning_summary"]),
        source_facts=list(data.get("source_facts") or []),
        requires_review=bool(data.get("requires_review", True)),
    )


def policy_gate_resolution(
    resolution: LLMFieldResolution,
    policy: FillPolicy,
    *,
    min_confidence: float = 0.60,
) -> LLMFieldResolution:
    """Reject unsafe / low-confidence / sensitive autonomous resolutions."""
    if resolution.confidence < min_confidence:
        resolution.action_type = "manual_required"
        resolution.requires_review = True
        resolution.proposed_value = None
        return resolution

    if resolution.category in SENSITIVE_CATEGORIES or resolution.category in EXACT_ANSWER_CATEGORIES:
        # LLM must never resolve sensitive categories autonomously
        if resolution.category.value not in policy.approved_answers:
            resolution.action_type = "manual_required"
            resolution.proposed_value = None
            resolution.requires_review = True
            return resolution

    if resolution.category == FieldCategory.NARRATIVE and not policy.allow_narrative_auto_fill:
        resolution.action_type = "draft_only"
        resolution.requires_review = True

    if resolution.action_type not in ALLOWED_ACTIONS:
        raise LLMFallbackError("action_rejected_by_policy")

    # Never invent facts — require source_facts for non-skip actions with values
    if (
        resolution.proposed_value not in (None, "")
        and resolution.action_type in {"fill_text", "select_option", "choose_radio", "draft_only"}
        and not resolution.source_facts
        and resolution.category
        not in {
            FieldCategory.FIRST_NAME,
            FieldCategory.LAST_NAME,
            FieldCategory.EMAIL,
            FieldCategory.PHONE,
        }
    ):
        resolution.action_type = "manual_required"
        resolution.requires_review = True

    return resolution


def build_classification_prompt(field: FormField, profile_paths: list[str]) -> str:
    return (
        "Classify this job-application form field. Return ONLY a JSON object with keys: "
        "field_id, category, profile_path, confidence, action_type, proposed_value, "
        "reasoning_summary, source_facts (array of strings from the profile), requires_review.\n"
        "action_type must be one of: fill_text, select_option, choose_radio, check, draft_only, skip, manual_required.\n"
        "Do NOT invent candidate facts. If unsure, use manual_required.\n"
        f"field_id: {field.field_id}\n"
        f"label: {field.label}\n"
        f"name: {field.name}\n"
        f"autocomplete: {field.autocomplete}\n"
        f"widget: {field.widget_type.value}\n"
        f"nearby: {field.nearby_text}\n"
        f"allowed_profile_paths: {profile_paths}\n"
    )


async def resolve_field_with_llm(
    field: FormField,
    *,
    policy: FillPolicy,
    ask: Callable[[str], str] | None = None,
    profile_paths: list[str] | None = None,
) -> LLMFieldResolution:
    """Call LLM (or injected ask()) and validate/gate the result."""
    if ask is None:
        from applypilot.llm import get_client

        client = get_client()

        def ask(prompt: str) -> str:
            return client.ask(prompt)

    prompt = build_classification_prompt(field, profile_paths or [])
    raw = ask(prompt)
    data = _extract_json(raw)
    if "field_id" not in data:
        data["field_id"] = field.field_id
    resolution = parse_llm_resolution(data)
    return policy_gate_resolution(
        resolution, policy, min_confidence=policy.llm_classification_threshold
    )
