"""ATS adapter abstract base."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from applypilot.apply.form_engine.models import (
    ATSDetection,
    ApplicationState,
    FillAction,
    FormIR,
    SubmissionEvidence,
)
from applypilot.apply.form_engine.policy import FillPolicy


class ATSAdapter(ABC):
    ats_name: str = "generic"

    @abstractmethod
    async def detect(self, page: Any) -> ATSDetection:
        ...

    @abstractmethod
    async def wait_until_ready(self, page: Any) -> None:
        ...

    @abstractmethod
    async def detect_state(self, page: Any) -> ApplicationState:
        ...

    @abstractmethod
    async def scan_form(self, page: Any) -> FormIR:
        ...

    @abstractmethod
    async def build_adapter_fill_plan(
        self,
        form_ir: FormIR,
        profile: dict,
        policy: FillPolicy,
    ) -> list[FillAction]:
        ...

    @abstractmethod
    async def find_next_button(self, page: Any) -> Any | None:
        ...

    @abstractmethod
    async def find_submit_button(self, page: Any) -> Any | None:
        ...

    @abstractmethod
    async def detect_submission_confirmation(self, page: Any) -> SubmissionEvidence:
        ...
