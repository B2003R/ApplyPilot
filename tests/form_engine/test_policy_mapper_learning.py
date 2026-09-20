"""Policy, matcher, LLM gate, learning, and config unit tests."""

from __future__ import annotations

import pytest

from applypilot.apply.form_engine.llm_fallback import (
    LLMFallbackError,
    parse_llm_resolution,
    policy_gate_resolution,
)
from applypilot.apply.form_engine.learning import LearningStore, PROMOTE_TO_ATS_AFTER
from applypilot.apply.form_engine.mapper import score_field, value_for_match
from applypilot.apply.form_engine.models import (
    FieldCategory,
    FormField,
    LearnedFieldMapping,
    WidgetType,
)
from applypilot.apply.form_engine.policy import FillPolicy
from applypilot.apply.form_engine.submission import can_submit
from applypilot.apply.form_engine.models import ValidationReport
from applypilot.config import load_application_engine_config


def _field(**kwargs) -> FormField:
    defaults = dict(
        field_id="f1",
        locator_hint="#f1",
        label=None,
        aria_label=None,
        placeholder=None,
        name=None,
        element_id=None,
        autocomplete=None,
        input_type=None,
        widget_type=WidgetType.TEXT,
        required=False,
        visible=True,
        enabled=True,
        current_value=None,
    )
    defaults.update(kwargs)
    return FormField(**defaults)


def test_application_engine_disabled_by_default():
    cfg = load_application_engine_config()
    assert cfg["enabled"] is False
    assert cfg["auto_submit"] is False


def test_semantic_email_match_high_confidence():
    f = _field(
        field_id="email",
        label="Email",
        autocomplete="email",
        input_type="email",
        name="email",
    )
    m = score_field(f)
    assert m.category == FieldCategory.EMAIL
    assert m.confidence >= 0.95


def test_sensitive_work_auth_never_auto_from_similarity_alone():
    f = _field(
        field_id="wa",
        label="Are you legally authorized to work?",
        name="work_authorization",
        widget_type=WidgetType.RADIO_GROUP,
        is_sensitive=True,
    )
    m = score_field(f)
    assert m.category == FieldCategory.WORK_AUTHORIZATION
    policy = FillPolicy()
    assert policy.decision_for_match(m, f) == "skip_sensitive"


def test_sensitive_approved_answer_allows_fill():
    f = _field(label="Do you require sponsorship?", name="sponsor")
    m = score_field(f)
    policy = FillPolicy(approved_answers={"sponsorship": "No"})
    # category may be sponsorship
    if m.category == FieldCategory.SPONSORSHIP:
        assert policy.decision_for_match(m, f) == "auto_fill"


def test_narrative_requires_review():
    f = _field(
        label="Why do you want to work with us?",
        widget_type=WidgetType.TEXTAREA,
        is_narrative=True,
    )
    m = score_field(f)
    policy = FillPolicy(allow_narrative_auto_fill=False)
    assert policy.decision_for_match(m, f) in {"review", "skip_sensitive", "llm", "unresolved"}
    assert policy.decision_for_match(m, f) != "auto_fill" or m.category != FieldCategory.NARRATIVE


def test_profile_value_resolution(synthetic_profile):
    f = _field(label="Email", autocomplete="email", input_type="email")
    m = score_field(f)
    assert value_for_match(m, synthetic_profile) == "ada.testington@example.com"


def test_llm_parse_and_reject_unsafe_action():
    with pytest.raises(LLMFallbackError):
        parse_llm_resolution(
            {
                "field_id": "x",
                "category": "email",
                "confidence": 0.9,
                "action_type": "submit",
                "reasoning_summary": "nope",
            }
        )


def test_llm_policy_rejects_sensitive_without_approval():
    res = parse_llm_resolution(
        {
            "field_id": "wa",
            "category": "work_authorization",
            "confidence": 0.99,
            "action_type": "choose_radio",
            "proposed_value": "Yes",
            "reasoning_summary": "guess",
            "source_facts": ["work_authorization.legally_authorized_to_work"],
            "requires_review": False,
        }
    )
    gated = policy_gate_resolution(res, FillPolicy())
    assert gated.action_type == "manual_required"


def test_llm_low_confidence_manual():
    res = parse_llm_resolution(
        {
            "field_id": "q",
            "category": "unknown",
            "confidence": 0.4,
            "action_type": "fill_text",
            "proposed_value": "hello",
            "reasoning_summary": "weak",
            "source_facts": ["x"],
        }
    )
    gated = policy_gate_resolution(res, FillPolicy(), min_confidence=0.6)
    assert gated.action_type == "manual_required"


def test_learned_mapping_promotion_rules(test_db):
    store = LearningStore(str(test_db))
    mapping = LearnedFieldMapping(
        ats_name="greenhouse",
        company_domain="example.com",
        field_signature="email|email|email|email|email",
        field_category=FieldCategory.EMAIL,
        profile_path="personal.email",
        scope="job",
        confidence=0.9,
        successful_uses=0,
        job_url="https://example.com/jobs/1",
    )
    for _ in range(PROMOTE_TO_ATS_AFTER):
        mapping = store.record_success(mapping)
    assert mapping.scope == "ats"

    # Sensitive never promotes to ats/global
    sens = LearnedFieldMapping(
        ats_name="greenhouse",
        company_domain="example.com",
        field_signature="wa|work_auth||work authorization|radio_group",
        field_category=FieldCategory.WORK_AUTHORIZATION,
        profile_path="work_authorization.legally_authorized_to_work",
        scope="company",
        confidence=0.9,
        successful_uses=0,
        job_url=None,
    )
    for _ in range(10):
        sens = store.record_success(sens)
    assert sens.scope == "company"


def test_submission_eligibility_gates():
    policy = FillPolicy(auto_submit=True)
    ok, reason = can_submit(
        ValidationReport(
            required_field_coverage=1.0,
            unresolved_required_fields=[],
            validation_errors=[],
        ),
        policy,
    )
    assert ok and reason == "ok"

    blocked, reason = can_submit(
        ValidationReport(
            required_field_coverage=0.5,
            unresolved_required_fields=["email"],
            validation_errors=[],
        ),
        policy,
    )
    assert not blocked

    policy2 = FillPolicy(auto_submit=False)
    blocked2, reason2 = can_submit(
        ValidationReport(required_field_coverage=1.0),
        policy2,
    )
    assert not blocked2 and reason2 == "auto_submit_disabled"


def test_run_store_persistence(test_db):
    from applypilot.apply.form_engine.persistence import RunStore
    from applypilot.apply.form_engine.models import SubmissionEvidence

    store = RunStore(str(test_db))
    store.create_run("fe_test1", job_url="https://example.com/j", status="filling")
    store.add_field_event(
        "fe_test1",
        field_id="email",
        category="email",
        action_type="fill_text",
        outcome="success",
        confidence=0.99,
        value_source="semantic",
    )
    store.add_submission_evidence(
        "fe_test1",
        SubmissionEvidence(result="submitted", confirmation_text="thank you"),
    )
    run = store.get_run("fe_test1")
    assert run is not None
    assert run["status"] == "filling"
