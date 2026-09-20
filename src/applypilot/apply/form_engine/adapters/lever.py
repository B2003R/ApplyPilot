"""Lever ATS adapter — fixture-driven support.

Supports (against fixtures): standard application fields, resume upload input,
submit/confirmation detection.

Not claimed: live jobs.lever.co production validation.
"""

from __future__ import annotations

from typing import Any

from applypilot.apply.form_engine.adapters.base import ATSAdapter
from applypilot.apply.form_engine.adapters.generic import (
    build_semantic_fill_plan,
    common_detect_state,
    common_find_button,
    common_submission_evidence,
    generic_scan,
)
from applypilot.apply.form_engine.detector import detect_ats
from applypilot.apply.form_engine.models import (
    ATSDetection,
    ApplicationState,
    FillAction,
    FormIR,
    SubmissionEvidence,
)
from applypilot.apply.form_engine.policy import FillPolicy


class LeverAdapter(ATSAdapter):
    ats_name = "lever"

    async def detect(self, page: Any) -> ATSDetection:
        det = await detect_ats(page)
        if det.ats_name == "lever":
            return det
        try:
            if await page.locator(".application-form, [data-qa='btn-submit'], [data-ats='lever']").count() > 0:
                return ATSDetection("lever", 0.9, "lever_dom_markers")
        except Exception:
            pass
        return det

    async def wait_until_ready(self, page: Any) -> None:
        try:
            await page.wait_for_selector(
                "form, .application-form, [data-ats='lever'], input",
                timeout=5000,
            )
        except Exception:
            pass

    async def detect_state(self, page: Any) -> ApplicationState:
        return await common_detect_state(page)

    async def scan_form(self, page: Any) -> FormIR:
        return await generic_scan(page, ats_name=self.ats_name)

    async def build_adapter_fill_plan(
        self,
        form_ir: FormIR,
        profile: dict,
        policy: FillPolicy,
    ) -> list[FillAction]:
        actions, _, _ = build_semantic_fill_plan(form_ir, profile, policy)
        for a in actions:
            if a.source == "semantic":
                a.source = "adapter"
                a.metadata["adapter"] = self.ats_name
        return actions

    async def find_next_button(self, page: Any) -> Any | None:
        return await common_find_button(page, ["Next", "Continue"])

    async def find_submit_button(self, page: Any) -> Any | None:
        return await common_find_button(page, ["Submit application", "Submit", "Apply"])

    async def detect_submission_confirmation(self, page: Any) -> SubmissionEvidence:
        return await common_submission_evidence(page)
