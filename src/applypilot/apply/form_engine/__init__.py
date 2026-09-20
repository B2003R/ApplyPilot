"""ATS-aware form-filling engine (opt-in).

Default ApplyPilot apply path (Claude Code + Playwright MCP) is unchanged.
Enable via application_engine.enabled or ``applypilot apply --form-engine``.
"""

from applypilot.apply.form_engine.models import (
    ApplicationState,
    ATSDetection,
    EngineResult,
    FieldCategory,
    FieldMatch,
    FormField,
    FormIR,
    FormOption,
    WidgetType,
)
from applypilot.apply.form_engine.orchestrator import (
    run_form_engine_job,
    run_form_engine_on_page,
)
from applypilot.apply.form_engine.policy import FillPolicy

__all__ = [
    "ATSDetection",
    "ApplicationState",
    "EngineResult",
    "FieldCategory",
    "FieldMatch",
    "FillPolicy",
    "FormField",
    "FormIR",
    "FormOption",
    "WidgetType",
    "run_form_engine_job",
    "run_form_engine_on_page",
]
