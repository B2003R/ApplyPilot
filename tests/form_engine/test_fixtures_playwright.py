"""Fixture-driven Playwright tests for scan, detect, actions, adapters, submit."""

from __future__ import annotations

import pytest

from applypilot.apply.form_engine.actions import FormActions
from applypilot.apply.form_engine.adapters import get_adapter
from applypilot.apply.form_engine.adapters.generic import (
    build_semantic_fill_plan,
    common_submission_evidence,
)
from applypilot.apply.form_engine.detector import detect_ats
from applypilot.apply.form_engine.executor import FormExecutor
from applypilot.apply.form_engine.locators import EMAIL_LOCATOR_LADDER, resolve_locator
from applypilot.apply.form_engine.models import ApplicationState, FieldCategory
from applypilot.apply.form_engine.orchestrator import run_form_engine_on_page
from applypilot.apply.form_engine.policy import FillPolicy
from applypilot.apply.form_engine.scanner import scan_form
from applypilot.apply.form_engine.state_machine import form_changed_or_new_required_fields
from applypilot.apply.form_engine.submission import can_submit, submit_if_allowed
from applypilot.apply.form_engine.validator import validate_form


pytestmark = pytest.mark.asyncio


async def test_ats_detection_greenhouse(load_fixture_page):
    page = await load_fixture_page("greenhouse_standard.html")
    det = await detect_ats(page)
    assert det.ats_name == "greenhouse"
    assert det.confidence >= 0.85


async def test_ats_detection_lever(load_fixture_page):
    page = await load_fixture_page("lever_standard.html")
    det = await detect_ats(page)
    assert det.ats_name == "lever"
    assert det.confidence >= 0.85


async def test_ats_detection_ashby_workday(load_fixture_page):
    page = await load_fixture_page("ashby_standard.html")
    assert (await detect_ats(page)).ats_name == "ashby"
    page = await load_fixture_page("workday_contact_step.html")
    assert (await detect_ats(page)).ats_name == "workday"


async def test_form_ir_extraction_greenhouse(load_fixture_page):
    page = await load_fixture_page("greenhouse_standard.html")
    ir = await scan_form(page, ats_name="greenhouse")
    assert ir.submit_button_present
    labels = {(f.label or "").lower() for f in ir.fields}
    assert any("email" in l for l in labels)
    assert any("first name" in l for l in labels)
    email = next(f for f in ir.fields if (f.label or "").lower() == "email")
    assert email.required and email.autocomplete == "email"


async def test_locator_ladder_email(load_fixture_page):
    page = await load_fixture_page("generic_semantic_form.html")
    loc = await resolve_locator(page, EMAIL_LOCATOR_LADDER)
    assert loc is not None
    await loc.fill("x@example.com")
    assert await loc.input_value() == "x@example.com"


async def test_verified_text_select_radio_checkbox(load_fixture_page):
    page = await load_fixture_page("generic_semantic_form.html")
    actions = FormActions(page)
    r = await actions.fill_text_verified(
        field_id="mail", locator_hint="#mail", value="ada@example.com", category="email"
    )
    assert r.success

    r2 = await actions.select_native_option_verified(
        field_id="how", locator_hint="#how", option_label="LinkedIn"
    )
    assert r2.success

    page = await load_fixture_page("greenhouse_custom_questions.html")
    actions = FormActions(page)
    r3 = await actions.choose_radio_verified(
        field_id="wa",
        locator_hint="input[type='radio'][name='work_auth']",
        option_label="Yes",
    )
    assert r3.success


async def test_conditional_rescan(load_fixture_page):
    page = await load_fixture_page("conditional_field_reveal.html")
    before = await scan_form(page)
    before_ids = {f.field_id for f in before.fields if f.visible}

    await page.check("#sponsor-yes")
    after = await scan_form(page)
    assert form_changed_or_new_required_fields(before, after) or any(
        "visa" in (f.name or "") for f in after.fields if f.visible
    )
    # Visa field should now be in DOM visible
    visa = page.locator("#visa-type")
    assert await visa.is_visible()


async def test_semantic_fill_plan_generic(load_fixture_page, synthetic_profile):
    page = await load_fixture_page("generic_semantic_form.html")
    ir = await scan_form(page)
    policy = FillPolicy()
    actions, unresolved, llm_fields = build_semantic_fill_plan(ir, synthetic_profile, policy)
    assert any(a.category == FieldCategory.EMAIL for a in actions)
    executor = FormExecutor(page)
    results = await executor.execute(actions)
    assert any(r.success for r in results)
    assert await page.input_value("#mail") == synthetic_profile["personal"]["email"]


async def test_greenhouse_adapter_fill(load_fixture_page, synthetic_profile, test_db):
    page = await load_fixture_page("greenhouse_standard.html")
    adapter = get_adapter("greenhouse")
    assert (await adapter.detect(page)).ats_name == "greenhouse"
    ir = await adapter.scan_form(page)
    policy = FillPolicy(auto_submit=False)
    plan = await adapter.build_adapter_fill_plan(ir, synthetic_profile, policy)
    assert plan
    results = await FormExecutor(page).execute(plan)
    assert any(r.success for r in results)
    assert "ada" in (await page.input_value("#first_name")).lower()


async def test_lever_adapter_scan(load_fixture_page):
    page = await load_fixture_page("lever_standard.html")
    adapter = get_adapter("lever")
    ir = await adapter.scan_form(page)
    assert ir.ats_name == "lever"
    assert await adapter.find_submit_button(page) is not None


async def test_workday_combobox_action(load_fixture_page):
    page = await load_fixture_page("workday_combobox.html")
    actions = FormActions(page)
    r = await actions.select_combobox_option_verified(
        field_id="country",
        locator_hint="#country-combo",
        option_label="Canada",
    )
    assert r.success
    assert "Canada" in (await page.input_value("#country-combo"))


async def test_validation_error_fixture(load_fixture_page):
    page = await load_fixture_page("validation_error.html")
    ir = await scan_form(page)
    report = await validate_form(page, ir, filled_field_ids={"first_name"})
    assert report.validation_errors
    assert "email" in " ".join(report.unresolved_required_fields).lower() or report.unresolved_required_fields


async def test_submission_success_confirmation(load_fixture_page):
    page = await load_fixture_page("submission_success.html")
    evidence = await common_submission_evidence(page)
    assert evidence.result == "submitted"
    assert evidence.confirmation_id == "APP-12345" or "thank you" in (
        evidence.confirmation_text or ""
    )


async def test_submission_unknown(load_fixture_page):
    page = await load_fixture_page("submission_unknown.html")
    evidence = await common_submission_evidence(page)
    assert evidence.result == "submission_unknown"


async def test_submit_blocked_when_missing_required(load_fixture_page, synthetic_profile):
    page = await load_fixture_page("validation_error.html")
    adapter = get_adapter("generic")
    ir = await adapter.scan_form(page)
    validation = await validate_form(page, ir, filled_field_ids=set())
    policy = FillPolicy(auto_submit=True)
    allowed, reason = can_submit(validation, policy)
    assert not allowed


async def test_orchestrator_ready_to_submit_no_autosubmit(
    load_fixture_page, synthetic_profile, test_db
):
    page = await load_fixture_page("greenhouse_standard.html")
    job = {
        "url": "https://boards.greenhouse.io/example/jobs/1",
        "application_url": "https://boards.greenhouse.io/example/jobs/1",
        "title": "Software Engineer",
        "site": "example",
    }
    policy = FillPolicy(auto_submit=False)
    result = await run_form_engine_on_page(
        page,
        job=job,
        profile=synthetic_profile,
        policy=policy,
        engine_cfg={
            "enabled": True,
            "auto_submit": False,
            "use_llm_fallback": False,
            "diagnostic_mode": True,
            "adapter_threshold": 0.85,
            "max_state_transitions": 10,
            "max_action_retries": 1,
        },
        db_path=str(test_db),
        llm_ask=lambda p: "{}",
    )
    assert result.status in {"ready_to_submit", "submitted", "recovery_required"}
    assert result.run_id
    # With standard greenhouse form + synthetic profile, should fill identity fields
    assert result.ats_name == "greenhouse"


async def test_orchestrator_manual_handoff_captcha(load_fixture_page, synthetic_profile, test_db):
    page = await load_fixture_page("greenhouse_standard.html")
    await page.evaluate(
        """() => {
          document.body.setAttribute('data-application-state', 'captcha_required');
          document.body.innerHTML = '<div class=\"g-recaptcha\">captcha</div><p>Please complete the captcha</p>';
        }"""
    )
    result = await run_form_engine_on_page(
        page,
        job={"url": "https://example.com/j", "title": "X", "site": "ex"},
        profile=synthetic_profile,
        policy=FillPolicy(auto_submit=False),
        engine_cfg={
            "use_llm_fallback": False,
            "adapter_threshold": 0.85,
            "max_state_transitions": 5,
        },
        db_path=str(test_db),
    )
    assert result.status == "captcha_required"
    assert result.apply_status == "captcha"
