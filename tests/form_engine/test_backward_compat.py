"""Backward compatibility: form engine disabled keeps Claude apply path."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from applypilot.config import load_application_engine_config


def test_form_engine_disabled_by_default():
    assert load_application_engine_config()["enabled"] is False


def test_worker_loop_uses_run_job_when_disabled():
    """When form_engine=False, worker_loop must call run_job (Claude path)."""
    job = {
        "url": "https://example.com/jobs/1",
        "title": "Engineer",
        "site": "example",
        "application_url": "https://example.com/jobs/1",
        "fit_score": 9,
        "tailored_resume_path": "/tmp/resume.txt",
        "cover_letter_path": None,
        "location": "Remote",
        "full_description": "desc",
    }

    with (
        patch("applypilot.apply.launcher.acquire_job", return_value=job),
        patch("applypilot.apply.launcher.launch_chrome", return_value=MagicMock()),
        patch("applypilot.apply.launcher.cleanup_worker"),
        patch("applypilot.apply.launcher.run_job", return_value=("applied", 100)) as mock_run,
        patch("applypilot.apply.launcher.mark_result"),
        patch("applypilot.apply.launcher.init_worker"),
        patch("applypilot.apply.launcher.update_state"),
        patch("applypilot.apply.launcher.add_event"),
        patch(
            "applypilot.apply.launcher.config.load_application_engine_config",
            return_value={"enabled": False},
        ),
    ):
        from applypilot.apply.launcher import worker_loop

        applied, failed = worker_loop(
            worker_id=0, limit=1, form_engine=False, dry_run=True
        )
        mock_run.assert_called_once()
        assert applied == 1
        assert failed == 0


def test_worker_loop_uses_form_engine_when_enabled():
    job = {
        "url": "https://example.com/jobs/1",
        "title": "Engineer",
        "site": "example",
        "application_url": "https://example.com/jobs/1",
        "fit_score": 9,
        "tailored_resume_path": "/tmp/resume.txt",
        "cover_letter_path": None,
        "location": "Remote",
        "full_description": "desc",
    }

    with (
        patch("applypilot.apply.launcher.acquire_job", return_value=job),
        patch("applypilot.apply.launcher.launch_chrome", return_value=MagicMock()),
        patch("applypilot.apply.launcher.cleanup_worker"),
        patch("applypilot.apply.launcher.run_job") as mock_claude,
        patch(
            "applypilot.apply.form_engine.run_form_engine_job",
            return_value=("applied", 50),
        ) as mock_fe,
        patch("applypilot.apply.launcher.mark_result"),
        patch("applypilot.apply.launcher.init_worker"),
        patch("applypilot.apply.launcher.update_state"),
        patch("applypilot.apply.launcher.add_event"),
    ):
        from applypilot.apply.launcher import worker_loop

        applied, failed = worker_loop(
            worker_id=0, limit=1, form_engine=True, dry_run=True
        )
        mock_fe.assert_called_once()
        mock_claude.assert_not_called()
        assert applied == 1
