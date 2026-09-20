"""Shared adapter helpers + generic fill-plan builder."""

from __future__ import annotations

import re
from typing import Any

from applypilot.apply.form_engine.mapper import match_form, value_for_match
from applypilot.apply.form_engine.models import (
    ApplicationState,
    FieldCategory,
    FillAction,
    FormField,
    FormIR,
    SubmissionEvidence,
    WidgetType,
)
from applypilot.apply.form_engine.policy import FillPolicy
from applypilot.apply.form_engine.scanner import scan_form


async def common_detect_state(page: Any) -> ApplicationState:
    """Heuristic application-state detection shared by adapters."""
    url = ""
    try:
        url = (page.url or "").lower()
    except Exception:
        pass

    text = ""
    try:
        text = (await page.locator("body").inner_text()).lower()
    except Exception:
        text = ""

    # data-state fixture hook
    try:
        state_el = page.locator("[data-application-state]")
        if await state_el.count() > 0:
            raw = await state_el.first.get_attribute("data-application-state")
            if raw:
                try:
                    return ApplicationState(raw)
                except ValueError:
                    pass
    except Exception:
        pass

    if any(k in text for k in ("captcha", "recaptcha", "h-captcha", "cf-turnstile")):
        return ApplicationState.CAPTCHA_REQUIRED
    if any(k in text for k in ("two-factor", "2fa", "one-time", "verification code", "enter the code")):
        return ApplicationState.MFA_OR_OTP_REQUIRED
    if any(k in text for k in ("sign in", "log in", "login")) and "password" in text:
        return ApplicationState.LOGIN_REQUIRED
    if any(
        k in text
        for k in (
            "thank you for applying",
            "application received",
            "application submitted",
            "successfully submitted",
            "we received your application",
        )
    ):
        return ApplicationState.SUBMITTED
    if "review your application" in text or "review and submit" in text:
        return ApplicationState.REVIEW_PAGE
    if "apply" in url and ("job" in url or "opening" in url):
        # Could still be entry — check for form fields
        pass

    # Form fields present?
    try:
        n = await page.locator("input, textarea, select").count()
        if n > 0:
            return ApplicationState.APPLICATION_FORM
    except Exception:
        pass

    if "apply" in text or "application" in text:
        return ApplicationState.APPLY_ENTRY
    return ApplicationState.JOB_PAGE


async def common_find_button(page: Any, names: list[str]) -> Any | None:
    for name in names:
        try:
            loc = page.get_by_role("button", name=re.compile(re.escape(name), re.I))
            if await loc.count() > 0:
                return loc.first
        except Exception:
            pass
        try:
            loc = page.locator(
                f"button:has-text('{name}'), input[type='submit'][value*='{name}' i], a:has-text('{name}')"
            )
            if await loc.count() > 0:
                return loc.first
        except Exception:
            pass
    return None


async def common_submission_evidence(page: Any) -> SubmissionEvidence:
    url = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    text = ""
    try:
        text = await page.locator("body").inner_text()
    except Exception:
        text = ""
    text_l = text.lower()

    success_phrases = [
        "thank you for applying",
        "application received",
        "application submitted",
        "successfully submitted",
        "we received your application",
        "thanks for applying",
    ]
    for phrase in success_phrases:
        if phrase in text_l:
            # Optional confirmation id
            conf_id = None
            m = re.search(r"(confirmation|reference|application)\s*(id|#|number)[:\s]*([A-Za-z0-9-]+)", text, re.I)
            if m:
                conf_id = m.group(3)
            return SubmissionEvidence(
                result="submitted",
                confirmation_text=phrase,
                confirmation_url=url,
                confirmation_id=conf_id,
                details=[phrase],
            )

    # Fixture hook
    try:
        el = page.locator("[data-submission-result]")
        if await el.count() > 0:
            raw = await el.first.get_attribute("data-submission-result")
            if raw == "submitted":
                return SubmissionEvidence(result="submitted", confirmation_url=url, details=["data-attr"])
            if raw == "unknown":
                return SubmissionEvidence(result="submission_unknown", confirmation_url=url, details=["data-attr"])
    except Exception:
        pass

    # Form still present with errors?
    try:
        if await page.locator(".error, .field-error, [role='alert'], .validation-error").count() > 0:
            return SubmissionEvidence(
                result="failed",
                confirmation_url=url,
                details=["validation_errors_present"],
            )
    except Exception:
        pass

    # Submit button gone but no thank-you → unknown
    submit = await common_find_button(page, ["Submit", "Submit Application", "Apply"])
    form_inputs = 0
    try:
        form_inputs = await page.locator("form input, form textarea, form select").count()
    except Exception:
        pass
    if submit is None and form_inputs == 0:
        return SubmissionEvidence(
            result="submission_unknown",
            confirmation_url=url,
            details=["form_gone_without_confirmation_text"],
        )

    return SubmissionEvidence(result="not_submitted", confirmation_url=url, details=["no_confirmation"])


def _action_for_field(
    field: FormField,
    category: FieldCategory,
    value: Any,
    *,
    confidence: float,
    source: str,
    profile_path: str | None,
    requires_review: bool,
) -> FillAction | None:
    if value is None and category not in {FieldCategory.CONSENT}:
        return None

    widget = field.widget_type
    if widget in {WidgetType.TEXT, WidgetType.EMAIL, WidgetType.PHONE, WidgetType.URL, WidgetType.NUMBER, WidgetType.TEXTAREA}:
        return FillAction(
            field_id=field.field_id,
            action_type="fill_text",
            locator_hint=field.locator_hint,
            value=str(value),
            category=category,
            confidence=confidence,
            source=source,
            profile_path=profile_path,
            requires_review=requires_review,
        )
    if widget == WidgetType.DATE:
        return FillAction(
            field_id=field.field_id,
            action_type="fill_date",
            locator_hint=field.locator_hint,
            value=str(value),
            category=category,
            confidence=confidence,
            source=source,
            profile_path=profile_path,
            requires_review=requires_review,
        )
    if widget == WidgetType.SELECT:
        # Pick matching option label if possible
        label = str(value)
        for opt in field.options:
            if label.lower() in opt.label.lower() or (opt.value and label.lower() == opt.value.lower()):
                label = opt.label
                break
        return FillAction(
            field_id=field.field_id,
            action_type="select_option",
            locator_hint=field.locator_hint,
            value=label,
            option_label=label,
            category=category,
            confidence=confidence,
            source=source,
            profile_path=profile_path,
            requires_review=requires_review,
        )
    if widget == WidgetType.COMBOBOX:
        return FillAction(
            field_id=field.field_id,
            action_type="select_option",
            locator_hint=field.locator_hint,
            value=str(value),
            option_label=str(value),
            category=category,
            confidence=confidence,
            source=source,
            profile_path=profile_path,
            requires_review=requires_review,
        )
    if widget == WidgetType.RADIO_GROUP:
        return FillAction(
            field_id=field.field_id,
            action_type="choose_radio",
            locator_hint=field.locator_hint,
            value=str(value),
            option_label=str(value),
            category=category,
            confidence=confidence,
            source=source,
            profile_path=profile_path,
            requires_review=requires_review,
        )
    if widget == WidgetType.CHECKBOX:
        return FillAction(
            field_id=field.field_id,
            action_type="check",
            locator_hint=field.locator_hint,
            value=True if value in (None, True, "Yes", "yes", "true", "1") else bool(value),
            category=category,
            confidence=confidence,
            source=source,
            profile_path=profile_path,
            requires_review=requires_review,
        )
    if widget == WidgetType.FILE:
        return FillAction(
            field_id=field.field_id,
            action_type="upload_file",
            locator_hint=field.locator_hint,
            value=str(value),
            category=category,
            confidence=confidence,
            source=source,
            profile_path=profile_path,
            requires_review=requires_review,
        )
    if widget == WidgetType.RICH_TEXT:
        return FillAction(
            field_id=field.field_id,
            action_type="fill_rich_text",
            locator_hint=field.locator_hint,
            value=str(value),
            category=category,
            confidence=confidence,
            source=source,
            profile_path=profile_path,
            requires_review=requires_review,
        )
    return FillAction(
        field_id=field.field_id,
        action_type="fill_text",
        locator_hint=field.locator_hint,
        value=str(value) if value is not None else None,
        category=category,
        confidence=confidence,
        source=source,
        profile_path=profile_path,
        requires_review=True,
    )


def build_semantic_fill_plan(
    form_ir: FormIR,
    profile: dict,
    policy: FillPolicy,
) -> tuple[list[FillAction], list[str], list[FormField]]:
    """Build fill plan from semantic matches + policy.

    Returns (actions, unresolved_field_ids, llm_candidate_fields).
    """
    matches = match_form(form_ir, policy)
    by_id = {m.field_id: m for m in matches}
    actions: list[FillAction] = []
    unresolved: list[str] = []
    llm_fields: list[FormField] = []

    for field in form_ir.fields:
        if not field.visible or not field.enabled:
            continue
        if field.widget_type == WidgetType.BUTTON:
            continue
        match = by_id.get(field.field_id)
        if match is None:
            unresolved.append(field.field_id)
            continue

        decision = policy.decision_for_match(match, field)
        if decision == "skip_sensitive":
            unresolved.append(field.field_id)
            continue
        if decision == "review":
            unresolved.append(field.field_id)
            continue
        if decision == "llm":
            llm_fields.append(field)
            continue
        if decision == "unresolved":
            unresolved.append(field.field_id)
            continue

        # auto_fill
        if match.category in {
            FieldCategory.WORK_AUTHORIZATION,
            FieldCategory.SPONSORSHIP,
            FieldCategory.LEGAL,
            FieldCategory.CONSENT,
            FieldCategory.DEMOGRAPHIC,
            FieldCategory.SALARY,
        }:
            value = policy.exact_answer(match.category)
            if value is None:
                value = value_for_match(match, profile)
        else:
            value = value_for_match(match, profile)

        action = _action_for_field(
            field,
            match.category,
            value,
            confidence=match.confidence,
            source="semantic",
            profile_path=match.profile_path,
            requires_review=match.confidence < 0.95,
        )
        if action is None:
            unresolved.append(field.field_id)
        else:
            actions.append(action)

    return actions, unresolved, llm_fields


async def generic_scan(page: Any, ats_name: str = "generic") -> FormIR:
    state = await common_detect_state(page)
    return await scan_form(page, ats_name=ats_name, application_state=state)
