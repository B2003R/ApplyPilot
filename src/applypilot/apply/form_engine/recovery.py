"""Structured recovery / handoff results for the form engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from applypilot.apply.form_engine.models import FormIR


@dataclass
class ManualHandoffRequired:
    reason: str
    state: str
    page_url: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    resumable: bool = True


@dataclass
class RecoveryRequired:
    reason: str
    unresolved_required_fields: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    form_ir: FormIR | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    transitions_used: int = 0


@dataclass
class ReadyToSubmit:
    form_ir: FormIR
    required_field_coverage: float
    message: str = "All required fields resolved; ready to submit"


@dataclass
class LoginHandoffResult:
    """Result of calling the existing ApplyPilot login integration hook."""

    success: bool
    still_login_required: bool = False
    message: str = ""
