"""Workday ATS adapter — detection/state/scan + combobox fixture support.

Supports (against fixtures): contact-step Form IR, ARIA combobox option select
via shared FormActions, multi-step next-button discovery.

Not claimed: live myworkdayjobs.com production interactions, SSO, or full
multi-page Workday career site flows.
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


class WorkdayAdapter(ATSAdapter):
    ats_name = "workday"

    async def detect(self, page: Any) -> ATSDetection:
        det = await detect_ats(page)
        if det.ats_name == "workday":
            return det
        try:
            if await page.locator(
                "[data-automation-id], [data-uxi-widget-type], [data-ats='workday']"
            ).count() > 0:
                return ATSDetection("workday", 0.88, "workday_dom_markers")
        except Exception:
            pass
        return det

    async def wait_until_ready(self, page: Any) -> None:
        try:
            await page.wait_for_selector(
                "[data-automation-id], form, [data-ats='workday'], input",
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
            a.metadata["adapter"] = self.ats_name
        return actions

    async def find_next_button(self, page: Any) -> Any | None:
        # Workday often uses data-automation-id
        try:
            loc = page.locator(
                "[data-automation-id='bottom-navigation-next-button'], "
                "button[data-automation-id*='next' i]"
            )
            if await loc.count() > 0:
                return loc.first
        except Exception:
            pass
        return await common_find_button(page, ["Next", "Continue"])

    async def find_submit_button(self, page: Any) -> Any | None:
        try:
            loc = page.locator("[data-automation-id='bottom-navigation-next-button'], button:has-text('Submit')")
            if await loc.count() > 0:
                # Prefer explicit Submit text
                submit = page.get_by_role("button", name="Submit")
                if await submit.count() > 0:
                    return submit.first
        except Exception:
            pass
        return await common_find_button(page, ["Submit", "Submit Application"])

    async def detect_submission_confirmation(self, page: Any) -> SubmissionEvidence:
        return await common_submission_evidence(page)
