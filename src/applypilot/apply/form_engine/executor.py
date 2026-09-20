"""Execute FillAction plans through verified FormActions."""

from __future__ import annotations

from typing import Any

from applypilot.apply.form_engine.actions import FormActions
from applypilot.apply.form_engine.models import ActionResult, FillAction


class FormExecutor:
    def __init__(self, page: Any, max_retries: int = 2):
        self.page = page
        self.actions = FormActions(page, max_retries=max_retries)

    async def execute(self, plan: list[FillAction]) -> list[ActionResult]:
        results: list[ActionResult] = []
        for action in plan:
            if action.action_type in {"skip", "draft_only", "manual_required"}:
                results.append(
                    ActionResult(
                        action.field_id,
                        success=action.action_type == "skip",
                        action_type=action.action_type,
                        error=None if action.action_type == "skip" else action.action_type,
                    )
                )
                continue

            cat = action.category.value if action.category else None
            result: ActionResult
            if action.action_type == "fill_text":
                result = await self.actions.fill_text_verified(
                    field_id=action.field_id,
                    locator_hint=action.locator_hint,
                    value=str(action.value or ""),
                    category=cat,
                )
            elif action.action_type == "select_option":
                # Try native then combobox
                label = action.option_label or str(action.value or "")
                result = await self.actions.select_native_option_verified(
                    field_id=action.field_id,
                    locator_hint=action.locator_hint,
                    option_label=label,
                    category=cat,
                )
                if not result.success:
                    result = await self.actions.select_combobox_option_verified(
                        field_id=action.field_id,
                        locator_hint=action.locator_hint,
                        option_label=label,
                        category=cat,
                    )
            elif action.action_type == "choose_radio":
                result = await self.actions.choose_radio_verified(
                    field_id=action.field_id,
                    locator_hint=action.locator_hint,
                    option_label=action.option_label or str(action.value or ""),
                    category=cat,
                )
            elif action.action_type == "check":
                result = await self.actions.set_checkbox_verified(
                    field_id=action.field_id,
                    locator_hint=action.locator_hint,
                    checked=True if action.value is None else bool(action.value),
                    category=cat,
                )
            elif action.action_type == "uncheck":
                result = await self.actions.set_checkbox_verified(
                    field_id=action.field_id,
                    locator_hint=action.locator_hint,
                    checked=False,
                    category=cat,
                )
            elif action.action_type == "fill_date":
                result = await self.actions.fill_date_verified(
                    field_id=action.field_id,
                    locator_hint=action.locator_hint,
                    value=str(action.value or ""),
                    category=cat,
                )
            elif action.action_type == "upload_file":
                result = await self.actions.upload_file_verified(
                    field_id=action.field_id,
                    locator_hint=action.locator_hint,
                    file_path=str(action.value or ""),
                    category=cat,
                )
            elif action.action_type == "fill_rich_text":
                result = await self.actions.fill_rich_text_verified(
                    field_id=action.field_id,
                    locator_hint=action.locator_hint,
                    value=str(action.value or ""),
                    category=cat,
                )
            elif action.action_type == "click_next":
                result = await self.actions.click_next_verified()
            elif action.action_type == "click_submit":
                result = await self.actions.click_submit_verified()
            else:
                result = ActionResult(
                    action.field_id, False, action.action_type, error="unknown_action"
                )
            results.append(result)
        return results
