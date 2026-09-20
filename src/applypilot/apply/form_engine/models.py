"""Typed models for the ATS-aware form-filling engine.

Uses dataclasses + Enums to match ApplyPilot conventions (no Pydantic dependency).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


class WidgetType(str, Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    NUMBER = "number"
    DATE = "date"
    SELECT = "select"
    COMBOBOX = "combobox"
    RADIO_GROUP = "radio_group"
    CHECKBOX = "checkbox"
    FILE = "file"
    RICH_TEXT = "rich_text"
    BUTTON = "button"
    UNKNOWN = "unknown"


class FieldCategory(str, Enum):
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    PREFERRED_NAME = "preferred_name"
    EMAIL = "email"
    PHONE = "phone"
    ADDRESS = "address"
    CITY = "city"
    STATE = "state"
    COUNTRY = "country"
    POSTAL_CODE = "postal_code"
    LINKEDIN = "linkedin"
    GITHUB = "github"
    PORTFOLIO = "portfolio"
    EDUCATION = "education"
    EXPERIENCE = "experience"
    SKILLS = "skills"
    RESUME = "resume"
    COVER_LETTER = "cover_letter"
    AVAILABILITY = "availability"
    WORK_AUTHORIZATION = "work_authorization"
    SPONSORSHIP = "sponsorship"
    CITIZENSHIP = "citizenship"
    CLEARANCE = "clearance"
    SALARY = "salary"
    DEMOGRAPHIC = "demographic"
    LEGAL = "legal"
    CONSENT = "consent"
    NARRATIVE = "narrative"
    UNKNOWN = "unknown"


class ApplicationState(str, Enum):
    JOB_PAGE = "job_page"
    APPLY_ENTRY = "apply_entry"
    LOGIN_REQUIRED = "login_required"
    MFA_OR_OTP_REQUIRED = "mfa_or_otp_required"
    CAPTCHA_REQUIRED = "captcha_required"
    APPLICATION_FORM = "application_form"
    FILLING = "filling"
    VALIDATING = "validating"
    REVIEW_PAGE = "review_page"
    READY_TO_SUBMIT = "ready_to_submit"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    SUBMISSION_UNKNOWN = "submission_unknown"
    ERROR = "error"
    MANUAL_HANDOFF_REQUIRED = "manual_handoff_required"
    RECOVERY_REQUIRED = "recovery_required"


SENSITIVE_CATEGORIES: frozenset[FieldCategory] = frozenset(
    {
        FieldCategory.WORK_AUTHORIZATION,
        FieldCategory.SPONSORSHIP,
        FieldCategory.CITIZENSHIP,
        FieldCategory.CLEARANCE,
        FieldCategory.DEMOGRAPHIC,
        FieldCategory.LEGAL,
        FieldCategory.CONSENT,
        FieldCategory.SALARY,
    }
)


@dataclass
class FormOption:
    label: str
    value: str | None = None
    selected: bool = False


@dataclass
class FormField:
    field_id: str
    locator_hint: str
    label: str | None
    aria_label: str | None
    placeholder: str | None
    name: str | None
    element_id: str | None
    autocomplete: str | None
    input_type: str | None
    widget_type: WidgetType
    required: bool
    visible: bool
    enabled: bool
    current_value: str | None
    options: list[FormOption] = field(default_factory=list)
    section: str | None = None
    nearby_text: str | None = None
    is_sensitive: bool = False
    is_narrative: bool = False
    is_repeated_section_field: bool = False
    frame_path: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FormIR:
    ats_name: str
    page_url: str
    application_state: ApplicationState
    stage_name: str | None
    fields: list[FormField]
    submit_button_present: bool
    next_button_present: bool
    validation_messages: list[str] = field(default_factory=list)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ATSDetection:
    ats_name: str
    confidence: float
    reason: str


@dataclass
class FieldMatch:
    field_id: str
    category: FieldCategory
    profile_path: str | None
    confidence: float
    explanation: str
    requires_review: bool
    is_sensitive: bool


@dataclass
class FillAction:
    """A single planned browser interaction (never unrestricted LLM actions)."""

    field_id: str
    action_type: Literal[
        "fill_text",
        "select_option",
        "choose_radio",
        "check",
        "uncheck",
        "fill_date",
        "upload_file",
        "fill_rich_text",
        "click_next",
        "click_submit",
        "draft_only",
        "skip",
        "manual_required",
    ]
    locator_hint: str
    value: str | bool | None = None
    option_label: str | None = None
    category: FieldCategory = FieldCategory.UNKNOWN
    confidence: float = 0.0
    source: str = "unknown"  # adapter | semantic | learned | llm | policy | human
    profile_path: str | None = None
    requires_review: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    field_id: str
    success: bool
    action_type: str
    verified_value: str | bool | None = None
    error: str | None = None
    retries: int = 0


@dataclass
class LLMFieldResolution:
    field_id: str
    category: FieldCategory
    profile_path: str | None
    confidence: float
    action_type: Literal[
        "fill_text",
        "select_option",
        "choose_radio",
        "check",
        "draft_only",
        "skip",
        "manual_required",
    ]
    proposed_value: str | bool | None
    reasoning_summary: str
    source_facts: list[str] = field(default_factory=list)
    requires_review: bool = True


@dataclass
class LearnedFieldMapping:
    ats_name: str | None
    company_domain: str | None
    field_signature: str
    field_category: FieldCategory
    profile_path: str | None
    scope: str  # job | company | ats | global
    confidence: float
    successful_uses: int = 0
    user_confirmed: bool = False
    job_url: str | None = None


@dataclass
class SubmissionEvidence:
    result: Literal[
        "submitted",
        "submission_unknown",
        "not_submitted",
        "blocked",
        "failed",
    ]
    confirmation_text: str | None = None
    confirmation_url: str | None = None
    confirmation_id: str | None = None
    details: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    required_field_coverage: float
    unresolved_required_fields: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    captcha_detected: bool = False
    mfa_pending: bool = False
    sensitive_unapproved: list[str] = field(default_factory=list)


@dataclass
class EngineResult:
    """Terminal outcome of a form-engine run."""

    status: str
    apply_status: str  # mapped to launcher vocabulary
    message: str
    run_id: str | None = None
    ats_name: str | None = None
    detection_confidence: float | None = None
    form_ir: FormIR | None = None
    evidence: SubmissionEvidence | None = None
    unresolved_fields: list[str] = field(default_factory=list)
    diagnostics_dir: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
