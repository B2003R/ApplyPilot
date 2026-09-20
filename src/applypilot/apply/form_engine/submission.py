"""Submission eligibility and confirmation controller."""

from __future__ import annotations

from typing import Any

from applypilot.apply.form_engine.actions import FormActions
from applypilot.apply.form_engine.adapters.base import ATSAdapter
from applypilot.apply.form_engine.models import SubmissionEvidence, ValidationReport
from applypilot.apply.form_engine.policy import FillPolicy


def can_submit(
    validation: ValidationReport,
    policy: FillPolicy,
    *,
    captcha_detected: bool = False,
    mfa_pending: bool = False,
) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    if not policy.auto_submit:
        return False, "auto_submit_disabled"
    if validation.required_field_coverage < 1.0:
        return False, "incomplete_required_coverage"
    if validation.unresolved_required_fields:
        return False, "unresolved_required_fields"
    if validation.validation_errors:
        return False, "validation_errors"
    if captcha_detected or validation.captcha_detected:
        return False, "captcha_detected"
    if mfa_pending or validation.mfa_pending:
        return False, "mfa_pending"
    if validation.sensitive_unapproved:
        return False, "sensitive_answers_not_approved"
    return True, "ok"


async def submit_if_allowed(
    page: Any,
    adapter: ATSAdapter,
    validation: ValidationReport,
    policy: FillPolicy,
    *,
    actions: FormActions | None = None,
) -> SubmissionEvidence:
    """Click submit at most once; never retry on unknown confirmation."""
    allowed, reason = can_submit(validation, policy)
    if not allowed:
        return SubmissionEvidence(result="blocked", details=[reason])

    btn = await adapter.find_submit_button(page)
    if btn is None:
        return SubmissionEvidence(result="failed", details=["submit_button_not_found"])

    act = actions or FormActions(page)
    click = await act.click_submit_verified()
    if not click.success:
        return SubmissionEvidence(result="failed", details=[click.error or "click_failed"])

    evidence = await adapter.detect_submission_confirmation(page)
    # Critical: never auto-retry submit when confirmation is unknown
    if evidence.result == "not_submitted":
        # Click happened but no confirmation — treat as unknown
        return SubmissionEvidence(
            result="submission_unknown",
            confirmation_url=evidence.confirmation_url,
            details=["submit_clicked_without_confirmation"],
        )
    return evidence
