"""Required-field validation against Form IR + page state."""

from __future__ import annotations

from typing import Any

from applypilot.apply.form_engine.models import FormIR, ValidationReport


async def validate_form(
    page: Any,
    form_ir: FormIR,
    *,
    filled_field_ids: set[str] | None = None,
    captcha_detected: bool = False,
    mfa_pending: bool = False,
    sensitive_unapproved: list[str] | None = None,
) -> ValidationReport:
    """Validate required fields and collect visible validation messages."""
    filled = filled_field_ids or set()
    unresolved: list[str] = []
    errors = list(form_ir.validation_messages)

    # Rescan live validation messages
    try:
        err_loc = page.locator(
            "[role='alert'], .error, .field-error, .validation-error, .invalid-feedback"
        )
        count = await err_loc.count()
        for i in range(min(count, 20)):
            txt = (await err_loc.nth(i).inner_text()).strip()
            if txt and txt not in errors:
                errors.append(txt)
    except Exception:
        pass

    required_fields = [f for f in form_ir.fields if f.required and f.visible and f.enabled]
    for f in required_fields:
        has_value = bool(f.current_value) or f.field_id in filled
        # Checkboxes/radios: selected option
        if f.widget_type.value == "checkbox":
            has_value = has_value or f.field_id in filled
        if f.widget_type.value == "radio_group":
            has_value = has_value or any(o.selected for o in f.options) or f.field_id in filled
        if not has_value:
            unresolved.append(f.field_id)

    total = len(required_fields) or 1
    coverage = 1.0 - (len(unresolved) / total) if required_fields else 1.0
    if not required_fields:
        coverage = 1.0

    return ValidationReport(
        required_field_coverage=max(0.0, min(1.0, coverage)),
        unresolved_required_fields=unresolved,
        validation_errors=errors,
        captcha_detected=captcha_detected,
        mfa_pending=mfa_pending,
        sensitive_unapproved=list(sensitive_unapproved or []),
    )
