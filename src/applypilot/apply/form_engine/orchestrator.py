"""Form-engine orchestrator — CDP attach + adaptive fill loop.

Preserves ApplyPilot Chrome ownership: connects over CDP to an already-launched
worker browser. Never bypasses CAPTCHA/MFA/OTP.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from applypilot.apply.form_engine.adapters import get_adapter
from applypilot.apply.form_engine.adapters.generic import build_semantic_fill_plan
from applypilot.apply.form_engine.detector import detect_ats
from applypilot.apply.form_engine.diagnostics import Diagnostics
from applypilot.apply.form_engine.executor import FormExecutor
from applypilot.apply.form_engine.learning import LearningStore
from applypilot.apply.form_engine.llm_fallback import resolve_field_with_llm
from applypilot.apply.form_engine.models import (
    ApplicationState,
    EngineResult,
    FieldCategory,
    FillAction,
    FormIR,
    LearnedFieldMapping,
    WidgetType,
)
from applypilot.apply.form_engine.persistence import RunStore
from applypilot.apply.form_engine.policy import FillPolicy
from applypilot.apply.form_engine.recovery import (
    LoginHandoffResult,
    ManualHandoffRequired,
    ReadyToSubmit,
    RecoveryRequired,
)
from applypilot.apply.form_engine.scanner import company_domain_from_url, field_signature
from applypilot.apply.form_engine.state_machine import (
    TransitionBudget,
    form_changed_or_new_required_fields,
)
from applypilot.apply.form_engine.submission import can_submit, submit_if_allowed
from applypilot.apply.form_engine.validator import validate_form
from applypilot import config

logger = logging.getLogger(__name__)

LoginHandler = Callable[[Any], Awaitable[LoginHandoffResult]]


async def default_login_handoff(page: Any) -> LoginHandoffResult:
    """Cloud-safe login integration: reuse session only; never bypass auth walls.

    Existing ApplyPilot login ownership remains with the Chrome worker profile /
    Claude prompt path. The form engine does not implement CAPTCHA/OTP/MFA.
    """
    # If cookies already authenticated, state detection will move off LOGIN_REQUIRED.
    return LoginHandoffResult(
        success=False,
        still_login_required=True,
        message="manual_login_required",
    )


def _map_apply_status(engine_status: str) -> str:
    """Map form-engine status to launcher vocabulary."""
    mapping = {
        "submitted": "applied",
        "ready_to_submit": "applied",  # dry-run / auto_submit false success path uses note
        "manual_handoff_required": "login_issue",
        "captcha_required": "captcha",
        "mfa_or_otp_required": "login_issue",
        "recovery_required": "failed:recovery_required",
        "submission_unknown": "failed:submission_unknown",
        "failed": "failed:form_engine",
        "error": "failed:form_engine_error",
        "blocked": "failed:submit_blocked",
    }
    if engine_status in mapping:
        return mapping[engine_status]
    if engine_status.startswith("failed"):
        return engine_status
    return f"failed:{engine_status}"


async def build_fill_plan(
    form_ir: FormIR,
    profile: dict,
    policy: FillPolicy,
    *,
    learned: LearningStore | None = None,
    ats_name: str | None = None,
    company_domain: str | None = None,
    job_url: str | None = None,
    use_llm: bool = True,
    llm_ask: Callable[[str], str] | None = None,
) -> tuple[list[FillAction], list[str]]:
    """Merge learned mappings, adapter/semantic plan, and constrained LLM fallback."""
    actions: list[FillAction] = []
    unresolved: list[str] = []
    covered: set[str] = set()

    # Learned mappings first
    if learned:
        for field in form_ir.fields:
            if not field.visible:
                continue
            sig = field_signature(field)
            m = learned.find_mapping(
                sig,
                ats_name=ats_name,
                company_domain=company_domain,
                job_url=job_url,
            )
            if not m:
                continue
            from applypilot.apply.form_engine.mapper import resolve_profile_value, value_for_match
            from applypilot.apply.form_engine.models import FieldMatch

            match = FieldMatch(
                field_id=field.field_id,
                category=m.field_category,
                profile_path=m.profile_path,
                confidence=m.confidence,
                explanation="learned_mapping",
                requires_review=False,
                is_sensitive=m.field_category
                in {
                    FieldCategory.WORK_AUTHORIZATION,
                    FieldCategory.SPONSORSHIP,
                    FieldCategory.DEMOGRAPHIC,
                    FieldCategory.LEGAL,
                    FieldCategory.CONSENT,
                    FieldCategory.SALARY,
                },
            )
            decision = policy.decision_for_match(match, field)
            if decision != "auto_fill":
                continue
            val = value_for_match(match, profile)
            if val is None:
                continue
            from applypilot.apply.form_engine.adapters.generic import _action_for_field

            act = _action_for_field(
                field,
                m.field_category,
                val,
                confidence=m.confidence,
                source="learned",
                profile_path=m.profile_path,
                requires_review=False,
            )
            if act:
                actions.append(act)
                covered.add(field.field_id)

    sem_actions, sem_unresolved, llm_fields = build_semantic_fill_plan(
        form_ir, profile, policy
    )
    for a in sem_actions:
        if a.field_id not in covered:
            actions.append(a)
            covered.add(a.field_id)

    for uid in sem_unresolved:
        if uid not in covered:
            unresolved.append(uid)

    # LLM for mid-confidence / ambiguous fields only
    if use_llm and policy.use_llm_fallback:
        for field in llm_fields:
            if field.field_id in covered:
                continue
            try:
                resolution = await resolve_field_with_llm(
                    field, policy=policy, ask=llm_ask
                )
            except Exception as e:
                logger.debug("LLM fallback failed for %s: %s", field.field_id, e)
                unresolved.append(field.field_id)
                continue

            if resolution.action_type in {"manual_required", "skip"}:
                unresolved.append(field.field_id)
                continue
            if resolution.action_type == "draft_only":
                unresolved.append(field.field_id)
                continue

            actions.append(
                FillAction(
                    field_id=field.field_id,
                    action_type=resolution.action_type,  # type: ignore[arg-type]
                    locator_hint=field.locator_hint,
                    value=resolution.proposed_value,
                    option_label=str(resolution.proposed_value)
                    if resolution.proposed_value is not None
                    else None,
                    category=resolution.category,
                    confidence=resolution.confidence,
                    source="llm",
                    profile_path=resolution.profile_path,
                    requires_review=resolution.requires_review,
                )
            )
            covered.add(field.field_id)

    return actions, unresolved


async def run_form_engine_on_page(
    page: Any,
    *,
    job: dict,
    profile: dict,
    policy: FillPolicy | None = None,
    engine_cfg: dict | None = None,
    db_path: str | None = None,
    login_handler: LoginHandler | None = None,
    llm_ask: Callable[[str], str] | None = None,
    start_url: str | None = None,
) -> EngineResult:
    """Run the adaptive form-fill loop on an existing Playwright page."""
    cfg = engine_cfg or config.load_application_engine_config()
    policy = policy or FillPolicy.from_config(cfg)
    login_handler = login_handler or default_login_handoff

    diag = Diagnostics(
        enabled=bool(cfg.get("diagnostic_mode", False)),
        trace_on_failure=bool(cfg.get("trace_on_failure", True)),
        screenshot_on_failure=bool(cfg.get("screenshot_on_failure", True)),
    )
    store = RunStore(db_path)
    learning = LearningStore(db_path)
    budget = TransitionBudget(int(cfg.get("max_state_transitions", 30)))
    adapter_threshold = float(cfg.get("adapter_threshold", 0.85))

    job_url = job.get("application_url") or job.get("url") or ""
    store.create_run(
        diag.run_id,
        job_url=job_url,
        job_title=job.get("title"),
        company=job.get("site"),
        status="opening",
    )

    if start_url:
        await page.goto(start_url, wait_until="domcontentloaded")

    detection = await detect_ats(page, url=start_url or job_url)
    ats_name = (
        detection.ats_name
        if detection.confidence >= adapter_threshold
        else "generic"
    )
    adapter = get_adapter(ats_name)
    store.update_run(
        diag.run_id,
        ats_name=ats_name,
        detection_confidence=detection.confidence,
        status="filling",
        page_url=getattr(page, "url", None),
    )

    await adapter.wait_until_ready(page)
    executor = FormExecutor(page, max_retries=int(cfg.get("max_action_retries", 2)))
    filled_ids: set[str] = set()
    last_state = ApplicationState.JOB_PAGE
    company_domain = company_domain_from_url(job_url)

    while True:
        if not budget.consume():
            store.update_run(diag.run_id, status="recovery_required")
            return EngineResult(
                status="recovery_required",
                apply_status=_map_apply_status("recovery_required"),
                message="max_state_transitions_exceeded",
                run_id=diag.run_id,
                ats_name=ats_name,
                detection_confidence=detection.confidence,
                diagnostics_dir=str(diag.dir),
            )

        state = await adapter.detect_state(page)
        diag.record_transition(last_state.value, state.value)
        last_state = state

        if state == ApplicationState.LOGIN_REQUIRED:
            store.update_run(diag.run_id, status="login_pending")
            handoff = await login_handler(page)
            if handoff.still_login_required or not handoff.success:
                store.update_run(diag.run_id, status="manual_handoff_required")
                return EngineResult(
                    status="manual_handoff_required",
                    apply_status=_map_apply_status("manual_handoff_required"),
                    message=handoff.message or "login_required",
                    run_id=diag.run_id,
                    ats_name=ats_name,
                    detection_confidence=detection.confidence,
                    diagnostics_dir=str(diag.dir),
                )
            continue

        if state in {
            ApplicationState.MFA_OR_OTP_REQUIRED,
            ApplicationState.CAPTCHA_REQUIRED,
        }:
            status = (
                "captcha_required"
                if state == ApplicationState.CAPTCHA_REQUIRED
                else "mfa_or_otp_required"
            )
            store.update_run(diag.run_id, status="manual_handoff_required")
            ManualHandoffRequired(
                reason=status, state=state.value, page_url=getattr(page, "url", None)
            )
            return EngineResult(
                status=status,
                apply_status=_map_apply_status(status),
                message=f"manual_handoff:{status}",
                run_id=diag.run_id,
                ats_name=ats_name,
                detection_confidence=detection.confidence,
                diagnostics_dir=str(diag.dir),
            )

        if state == ApplicationState.SUBMITTED:
            store.update_run(diag.run_id, status="submitted")
            return EngineResult(
                status="submitted",
                apply_status="applied",
                message="already_submitted_or_confirmed",
                run_id=diag.run_id,
                ats_name=ats_name,
                detection_confidence=detection.confidence,
                diagnostics_dir=str(diag.dir),
            )

        form_ir = await adapter.scan_form(page)
        adapter_plan = await adapter.build_adapter_fill_plan(form_ir, profile, policy)
        # Prefer adapter plan; also merge learned/LLM for gaps
        plan, unresolved = await build_fill_plan(
            form_ir,
            profile,
            policy,
            learned=learning,
            ats_name=ats_name,
            company_domain=company_domain,
            job_url=job_url,
            use_llm=bool(cfg.get("use_llm_fallback", True)),
            llm_ask=llm_ask,
        )
        # Merge: adapter actions win on field_id
        by_id = {a.field_id: a for a in plan}
        for a in adapter_plan:
            by_id[a.field_id] = a
        plan = list(by_id.values())

        results = await executor.execute(plan)
        for action, result in zip(plan, results):
            if result.success:
                filled_ids.add(action.field_id)
                # Learn non-sensitive mappings at job scope
                if action.source in {"adapter", "semantic", "llm"} and action.category not in {
                    FieldCategory.WORK_AUTHORIZATION,
                    FieldCategory.SPONSORSHIP,
                    FieldCategory.DEMOGRAPHIC,
                    FieldCategory.LEGAL,
                    FieldCategory.CONSENT,
                }:
                    field = next(
                        (f for f in form_ir.fields if f.field_id == action.field_id),
                        None,
                    )
                    if field:
                        mapping = LearnedFieldMapping(
                            ats_name=ats_name,
                            company_domain=company_domain,
                            field_signature=field_signature(field),
                            field_category=action.category,
                            profile_path=action.profile_path,
                            scope="job",
                            confidence=action.confidence,
                            successful_uses=0,
                            job_url=job_url,
                        )
                        learning.record_success(mapping)

            store.add_field_event(
                diag.run_id,
                field_id=action.field_id,
                category=action.category.value,
                action_type=action.action_type,
                outcome="success" if result.success else "failed",
                confidence=action.confidence,
                value_source=action.source,
                unresolved_reason=result.error,
                requires_review=action.requires_review,
            )
            diag.record_field_event(
                field_id=action.field_id,
                category=action.category.value,
                action=action.action_type,
                outcome="success" if result.success else "failed",
                source=action.source,
                confidence=action.confidence,
            )

        updated_ir = await adapter.scan_form(page)
        if form_changed_or_new_required_fields(form_ir, updated_ir):
            continue

        validation = await validate_form(
            page,
            updated_ir,
            filled_field_ids=filled_ids,
        )
        # Attach unresolved from planning for required fields
        for uid in unresolved:
            field = next((f for f in updated_ir.fields if f.field_id == uid), None)
            if field and field.required and uid not in filled_ids:
                if uid not in validation.unresolved_required_fields:
                    validation.unresolved_required_fields.append(uid)

        if validation.unresolved_required_fields:
            store.update_run(diag.run_id, status="recovery_required")
            RecoveryRequired(
                reason="unresolved_required_fields",
                unresolved_required_fields=validation.unresolved_required_fields,
                validation_errors=validation.validation_errors,
                form_ir=updated_ir,
                transitions_used=budget.used,
            )
            return EngineResult(
                status="recovery_required",
                apply_status=_map_apply_status("recovery_required"),
                message="unresolved_required_fields",
                run_id=diag.run_id,
                ats_name=ats_name,
                detection_confidence=detection.confidence,
                form_ir=updated_ir,
                unresolved_fields=validation.unresolved_required_fields,
                diagnostics_dir=str(diag.dir),
            )

        next_btn = await adapter.find_next_button(page)
        submit_btn = await adapter.find_submit_button(page)

        # Prefer next when both exist and not on review
        if next_btn is not None and state != ApplicationState.REVIEW_PAGE:
            # Avoid infinite next if submit is the same control — check text
            click = await executor.actions.click_next_verified()
            if not click.success and submit_btn is not None:
                # fall through to submit path
                pass
            else:
                continue

        if submit_btn is not None or updated_ir.submit_button_present:
            store.update_run(diag.run_id, status="ready_to_submit")
            ReadyToSubmit(form_ir=updated_ir, required_field_coverage=validation.required_field_coverage)

            allowed, reason = can_submit(validation, policy)
            if not allowed:
                # Dry-run / auto_submit false → success-like applied for dry runs handled by caller
                return EngineResult(
                    status="ready_to_submit",
                    apply_status="applied" if reason == "auto_submit_disabled" else _map_apply_status("blocked"),
                    message=reason,
                    run_id=diag.run_id,
                    ats_name=ats_name,
                    detection_confidence=detection.confidence,
                    form_ir=updated_ir,
                    diagnostics_dir=str(diag.dir),
                )

            store.update_run(diag.run_id, status="submitting")
            evidence = await submit_if_allowed(page, adapter, validation, policy)
            store.add_submission_evidence(diag.run_id, evidence)
            store.update_run(
                diag.run_id,
                status=evidence.result
                if evidence.result != "not_submitted"
                else "submission_unknown",
            )
            apply_status = (
                "applied"
                if evidence.result == "submitted"
                else _map_apply_status(
                    evidence.result
                    if evidence.result != "blocked"
                    else "blocked"
                )
            )
            return EngineResult(
                status=evidence.result,
                apply_status=apply_status,
                message="; ".join(evidence.details) if evidence.details else evidence.result,
                run_id=diag.run_id,
                ats_name=ats_name,
                detection_confidence=detection.confidence,
                form_ir=updated_ir,
                evidence=evidence,
                diagnostics_dir=str(diag.dir),
            )

        # No next/submit — done filling
        store.update_run(diag.run_id, status="ready_to_submit")
        return EngineResult(
            status="ready_to_submit",
            apply_status="applied",
            message="no_submit_control_form_filled",
            run_id=diag.run_id,
            ats_name=ats_name,
            detection_confidence=detection.confidence,
            form_ir=updated_ir,
            diagnostics_dir=str(diag.dir),
        )


async def run_form_engine_cdp(
    cdp_port: int,
    *,
    job: dict,
    profile: dict,
    policy: FillPolicy | None = None,
    engine_cfg: dict | None = None,
    db_path: str | None = None,
    login_handler: LoginHandler | None = None,
    llm_ask: Callable[[str], str] | None = None,
) -> EngineResult:
    """Attach to existing Chrome via CDP and run the form engine."""
    from playwright.async_api import async_playwright

    url = job.get("application_url") or job.get("url")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            return await run_form_engine_on_page(
                page,
                job=job,
                profile=profile,
                policy=policy,
                engine_cfg=engine_cfg,
                db_path=db_path,
                login_handler=login_handler,
                llm_ask=llm_ask,
                start_url=url,
            )
        finally:
            # Do not close the browser — Chrome ownership stays with apply/chrome.py
            pass


def run_form_engine_job(
    job: dict,
    port: int,
    worker_id: int = 0,
    *,
    dry_run: bool = False,
    engine_cfg: dict | None = None,
    profile: dict | None = None,
    db_path: str | None = None,
) -> tuple[str, int]:
    """Sync entrypoint used by launcher.worker_loop.

    Returns (apply_status_string, duration_ms) matching run_job().
    """
    import time

    from applypilot.config import load_profile

    cfg = dict(engine_cfg or config.load_application_engine_config())
    if dry_run:
        cfg["auto_submit"] = False

    policy = FillPolicy.from_config(cfg)
    # When dry_run, treat ready_to_submit as applied (parity with Claude dry-run)
    t0 = time.time()
    try:
        prof = profile if profile is not None else load_profile()
        # Attach resume/cover paths for file uploads
        if job.get("tailored_resume_path"):
            prof = dict(prof)
            prof["_resume_path"] = str(job["tailored_resume_path"]).replace(".txt", ".pdf")
            if not str(prof["_resume_path"]).endswith(".pdf"):
                prof["_resume_path"] = str(job["tailored_resume_path"])
        if job.get("cover_letter_path"):
            prof = dict(prof)
            prof["_cover_letter_path"] = job["cover_letter_path"]

        result = asyncio.run(
            run_form_engine_cdp(
                port,
                job=job,
                profile=prof,
                policy=policy,
                engine_cfg=cfg,
                db_path=db_path,
            )
        )
        duration_ms = int((time.time() - t0) * 1000)
        # dry_run ready_to_submit → applied
        if dry_run and result.status == "ready_to_submit":
            return "applied", duration_ms
        return result.apply_status, duration_ms
    except Exception as e:
        logger.exception("form engine failed for worker %s", worker_id)
        duration_ms = int((time.time() - t0) * 1000)
        return f"failed:form_engine:{str(e)[:80]}", duration_ms
