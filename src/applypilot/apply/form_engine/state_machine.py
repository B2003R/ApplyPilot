"""Adaptive multi-step helpers: change detection and transition budgets."""

from __future__ import annotations

from applypilot.apply.form_engine.models import FormField, FormIR


def form_changed_or_new_required_fields(before: FormIR, after: FormIR) -> bool:
    """True when rescan reveals structural change or new required fields."""
    before_ids = {f.field_id for f in before.fields if f.visible}
    after_ids = {f.field_id for f in after.fields if f.visible}
    if after_ids - before_ids:
        return True

    before_req = {f.field_id for f in before.fields if f.required and f.visible}
    after_req = {f.field_id for f in after.fields if f.required and f.visible}
    if after_req - before_req:
        return True

    # Stage name change
    if (before.stage_name or "") != (after.stage_name or ""):
        return True

    if before.application_state != after.application_state:
        return True

    return False


def required_fields(form_ir: FormIR) -> list[FormField]:
    return [f for f in form_ir.fields if f.required and f.visible and f.enabled]


class TransitionBudget:
    """Prevents infinite multi-step loops."""

    def __init__(self, max_transitions: int = 30):
        self.max_transitions = max_transitions
        self.used = 0

    def consume(self) -> bool:
        """Return True if still within budget after consuming one transition."""
        self.used += 1
        return self.used <= self.max_transitions

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_transitions
