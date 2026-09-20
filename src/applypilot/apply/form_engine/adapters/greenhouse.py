"""Greenhouse ATS adapter — fixture-driven support.

Supports (against fixtures): standard contact fields, custom questions scan,
submit/confirmation detection.

Not claimed: live boards.greenhouse.io production validation.
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


class GreenhouseAdapter(ATSAdapter):
    ats_name = "greenhouse"

    async def detect(self, page: Any) -> ATSDetection:
        det = await detect_ats(page)
        if det.ats_name == "greenhouse":
            return det
        # Soft boost if classic greenhouse markers exist
        try:
            if await page.locator("#application_form, #submit_app, [data-ats='greenhouse']").count() > 0:
                return ATSDetection("greenhouse", 0.9, "greenhouse_dom_markers")
        except Exception:
            pass
        return det

    async def wait_until_ready(self, page: Any) -> None:
        try:
            await page.wait_for_selector(
                "form, #application_form, [data-ats='greenhouse'], input, textarea",
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
        # Greenhouse-specific: prefer known field names
        actions, _unresolved, _llm = build_semantic_fill_plan(form_ir, profile, policy)
        # Annotate source
        for a in actions:
            if a.source == "semantic":
                a.source = "adapter"
                a.metadata["adapter"] = self.ats_name
        return actions

    async def find_next_button(self, page: Any) -> Any | None:
        return await common_find_button(page, ["Next", "Continue"])

    async def find_submit_button(self, page: Any) -> Any | None:
        return await common_find_button(page, ["Submit Application", "Submit", "Apply"])

    async def detect_submission_confirmation(self, page: Any) -> SubmissionEvidence:
        return await common_submission_evidence(page)
