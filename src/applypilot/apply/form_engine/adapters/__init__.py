"""Adapter registry."""

from __future__ import annotations

from typing import Any

from applypilot.apply.form_engine.adapters.ashby import AshbyAdapter
from applypilot.apply.form_engine.adapters.base import ATSAdapter
from applypilot.apply.form_engine.adapters.generic import (
    build_semantic_fill_plan,
    common_detect_state,
    common_find_button,
    common_submission_evidence,
    generic_scan,
)
from applypilot.apply.form_engine.adapters.greenhouse import GreenhouseAdapter
from applypilot.apply.form_engine.adapters.lever import LeverAdapter
from applypilot.apply.form_engine.adapters.workday import WorkdayAdapter
from applypilot.apply.form_engine.detector import detect_ats
from applypilot.apply.form_engine.models import (
    ATSDetection,
    ApplicationState,
    FillAction,
    FormIR,
    SubmissionEvidence,
)
from applypilot.apply.form_engine.policy import FillPolicy


class GenericAdapter(ATSAdapter):
    """Fallback adapter when ATS confidence is below threshold."""

    ats_name = "generic"

    async def detect(self, page: Any) -> ATSDetection:
        return await detect_ats(page)

    async def wait_until_ready(self, page: Any) -> None:
        try:
            await page.wait_for_selector("form, input, textarea, select", timeout=5000)
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
        return actions

    async def find_next_button(self, page: Any) -> Any | None:
        return await common_find_button(page, ["Next", "Continue"])

    async def find_submit_button(self, page: Any) -> Any | None:
        return await common_find_button(page, ["Submit", "Submit Application", "Apply"])

    async def detect_submission_confirmation(self, page: Any) -> SubmissionEvidence:
        return await common_submission_evidence(page)


ADAPTERS: dict[str, type[ATSAdapter]] = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "workday": WorkdayAdapter,
    "generic": GenericAdapter,
}


def get_adapter(ats_name: str) -> ATSAdapter:
    cls = ADAPTERS.get(ats_name, GenericAdapter)
    return cls()


__all__ = [
    "ATSAdapter",
    "ADAPTERS",
    "get_adapter",
    "GenericAdapter",
    "GreenhouseAdapter",
    "LeverAdapter",
    "AshbyAdapter",
    "WorkdayAdapter",
]
