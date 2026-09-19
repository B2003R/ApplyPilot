"""Tests for Claude auto-apply form-fill reliability (PR #3)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# Isolate user data BEFORE importing applypilot.config
TEST_HOME = Path(tempfile.mkdtemp(prefix="applypilot-formfill-"))
os.environ["APPLYPILOT_DIR"] = str(TEST_HOME)

from applypilot import config
from applypilot.apply import captcha
from applypilot.apply import prompt as prompt_mod
from applypilot.apply.chrome import _kill_process_tree
from applypilot.apply.launcher import (
    _is_permanent_failure,
    gen_prompt,
    mark_result,
    run_job,
)
from applypilot.config import is_manual_ats
from applypilot.database import init_db

MINIMAL_PDF = b"""%PDF-1.1
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000052 00000 n
0000000101 00000 n
trailer<</Size 4/Root 1 0 R>>
startxref
178
%%EOF
"""


PROFILE = {
    "personal": {
        "full_name": "Alex Rivera",
        "preferred_name": "Alex",
        "email": "alex.rivera@example.com",
        "password": "test-password",
        "phone": "555-123-4567",
        "address": "123 Main St",
        "city": "Hoboken",
        "province_state": "NJ",
        "country": "USA",
        "postal_code": "07030",
        "linkedin_url": "https://www.linkedin.com/in/alexrivera",
        "github_url": "https://github.com/alexrivera",
        "portfolio_url": "",
        "website_url": "",
    },
    "work_authorization": {
        "legally_authorized_to_work": "Yes",
        "require_sponsorship": "No",
        "work_permit_type": "US Citizen",
    },
    "availability": {
        "earliest_start_date": "Immediately",
        "available_for_full_time": "Yes",
        "available_for_contract": "No",
    },
    "compensation": {
        "salary_expectation": "120000",
        "salary_currency": "USD",
        "salary_range_min": "110000",
        "salary_range_max": "140000",
        "currency_conversion_note": "",
    },
    "experience": {
        "years_of_experience_total": "5",
        "education_level": "Master's Degree",
        "current_job_title": "Senior Backend Engineer",
        "current_title": "Senior Backend Engineer",
        "current_company": "Nimbus Labs",
        "target_role": "Staff Platform Engineer",
    },
    "skills_boundary": {
        "languages": ["Python", "Go", "SQL"],
        "frameworks": ["FastAPI", "Django"],
        "devops": ["Docker", "Kubernetes", "AWS"],
    },
    "resume_facts": {
        "preserved_companies": ["Nimbus Labs", "Acme Corp"],
        "preserved_projects": ["Event Bus Rewrite"],
        "preserved_school": "Stevens Institute of Technology",
        "real_metrics": ["cut p99 latency 40%"],
    },
    "eeo_voluntary": {
        "gender": "Decline to self-identify",
        "race_ethnicity": "Decline to self-identify",
        "veteran_status": "I am not a protected veteran",
        "disability_status": "I do not wish to answer",
    },
}


JOB_DESCRIPTION = textwrap.dedent(
    """
    We are hiring a Staff Platform Engineer to own Kubernetes platform
    reliability, CI/CD, and developer experience. You will work with Python
    and Go services, FastAPI internal tools, and AWS. Hybrid in Hoboken, NJ
    with remote flexibility for US-based candidates.
    """
).strip()


@pytest.fixture(autouse=True)
def _fresh_home(monkeypatch):
    TEST_HOME.mkdir(parents=True, exist_ok=True)
    (TEST_HOME / "profile.json").write_text(json.dumps(PROFILE), encoding="utf-8")
    (TEST_HOME / "searches.yaml").write_text(
        "location:\n  primary: Hoboken\n  accept_patterns: [Hoboken, NYC, New York]\n",
        encoding="utf-8",
    )
    config.ensure_dirs()
    yield


# ---------------------------------------------------------------------------
# CapSolver CLI + helper
# ---------------------------------------------------------------------------

class TestCaptchaCLI:
    def test_detect_js_prints_function(self):
        result = subprocess.run(
            [sys.executable, "-m", "applypilot.apply.captcha", "detect-js"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "() =>" in result.stdout
        assert "hcaptcha" in result.stdout
        assert "turnstile" in result.stdout
        assert "recaptcha" in result.stdout
        assert "funcaptcha" in result.stdout

    def test_solve_without_api_key_returns_clear_error(self, monkeypatch):
        monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
        # Ensure .env is empty
        (TEST_HOME / ".env").write_text("", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable, "-m", "applypilot.apply.captcha", "solve",
                "--type", "hcaptcha",
                "--url", "https://example.com/apply",
                "--sitekey", "test-sitekey",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "CAPSOLVER_API_KEY": "", "APPLYPILOT_DIR": str(TEST_HOME)},
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        assert payload["ok"] is False
        assert "CAPSOLVER_API_KEY" in payload["error"]

    def test_solve_unknown_type(self, monkeypatch):
        monkeypatch.setenv("CAPSOLVER_API_KEY", "dummy-key")
        out = captcha.solve("not-a-type", "https://example.com", "key")
        assert out["ok"] is False
        assert "Unknown captcha type" in out["error"]

    def test_solve_turnstile_script_only_does_not_call_api(self, monkeypatch):
        monkeypatch.setenv("CAPSOLVER_API_KEY", "dummy-key")
        with patch("applypilot.apply.captcha.httpx.Client") as client:
            out = captcha.solve("turnstile_script_only", "https://example.com", "key")
        assert out["ok"] is False
        assert "wait and re-detect" in out["error"]
        client.assert_not_called()

    def test_solve_success_returns_token_and_inject_js(self, monkeypatch):
        monkeypatch.setenv("CAPSOLVER_API_KEY", "dummy-key")

        class FakeResponse:
            def __init__(self, data):
                self._data = data

            def raise_for_status(self):
                return None

            def json(self):
                return self._data

        class FakeClient:
            def __init__(self, *a, **k):
                self.calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json):
                if url.endswith("createTask"):
                    return FakeResponse({"errorId": 0, "taskId": "task-1"})
                return FakeResponse({
                    "errorId": 0,
                    "status": "ready",
                    "solution": {"gRecaptchaResponse": "tok_abc"},
                })

        with patch("applypilot.apply.captcha.httpx.Client", FakeClient), \
             patch("applypilot.apply.captcha.time.sleep"):
            out = captcha.solve("hcaptcha", "https://jobs.example.com", "site-key")
        assert out["ok"] is True
        assert out["token"] == "tok_abc"
        assert "tok_abc" in out["inject_js"]
        assert out["inject_js"].startswith("() =>")

    def test_inject_js_escapes_quotes(self):
        js = captcha.build_inject_js("hcaptcha", "tok'en\\x")
        assert "\\'" in js or "tok\\'en" in js


# ---------------------------------------------------------------------------
# Permanent failure / retry policy
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    def test_captcha_is_retryable(self):
        assert _is_permanent_failure("captcha") is False
        assert _is_permanent_failure("failed:captcha") is False

    def test_login_issue_is_retryable(self):
        assert _is_permanent_failure("login_issue") is False
        assert _is_permanent_failure("failed:login_issue") is False

    def test_expired_is_permanent(self):
        assert _is_permanent_failure("expired") is True
        assert _is_permanent_failure("failed:expired") is True

    def test_other_permanent_reasons(self):
        assert _is_permanent_failure("already_applied") is True
        assert _is_permanent_failure("sso_required") is True
        assert _is_permanent_failure("cloudflare_blocked") is True
        assert _is_permanent_failure("failed:stuck") is False

    def test_mark_result_retryable_increments_attempts(self):
        db = TEST_HOME / "applypilot.db"
        if db.exists():
            db.unlink()
        init_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO jobs (url, title, tailored_resume_path, fit_score) VALUES (?,?,?,?)",
            ("https://jobs.example.com/1", "Engineer", "/tmp/r.pdf", 9),
        )
        conn.commit()
        conn.close()
        mark_result("https://jobs.example.com/1", "failed", "captcha", permanent=False)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT apply_attempts, apply_status, apply_error FROM jobs").fetchone()
        conn.close()
        assert row[0] == 1
        assert row[1] == "failed"
        assert row[2] == "captcha"


# ---------------------------------------------------------------------------
# Prompt context
# ---------------------------------------------------------------------------

class TestPromptContext:
    def test_profile_summary_includes_skills_and_facts(self):
        summary = prompt_mod._build_profile_summary(PROFILE)
        assert "Python" in summary
        assert "Kubernetes" in summary
        assert "Nimbus Labs" in summary
        assert "Event Bus Rewrite" in summary
        assert "Stevens Institute of Technology" in summary
        assert "cut p99 latency 40%" in summary
        assert "Current Title: Senior Backend Engineer" in summary
        assert "Current Company: Nimbus Labs" in summary

    def test_job_context_includes_location_and_truncated_jd(self):
        long_jd = "x" * 4000
        ctx = prompt_mod._build_job_context({
            "location": "Hoboken, NJ",
            "full_description": long_jd,
        })
        assert "Hoboken, NJ" in ctx
        assert "[truncated]" in ctx
        assert len(ctx) < 4500

    def test_build_prompt_injects_context_and_deconflicts_evaluate(self):
        resume_dir = TEST_HOME / "tailored_resumes"
        resume_dir.mkdir(parents=True, exist_ok=True)
        pdf = resume_dir / "job1.pdf"
        txt = resume_dir / "job1.txt"
        pdf.write_bytes(MINIMAL_PDF)
        txt.write_text("Alex Rivera - platform engineer at Nimbus Labs", encoding="utf-8")

        job = {
            "url": "https://jobs.example.com/staff-platform",
            "application_url": "https://jobs.example.com/staff-platform/apply",
            "title": "Staff Platform Engineer",
            "site": "ExampleCorp",
            "fit_score": 9,
            "location": "Hoboken, NJ",
            "full_description": JOB_DESCRIPTION,
            "tailored_resume_path": str(pdf),
        }
        built = prompt_mod.build_prompt(job, tailored_resume=txt.read_text())
        assert "Hoboken, NJ" in built
        assert "Kubernetes platform" in built
        assert "Python, Go, SQL" in built
        assert "Nimbus Labs" in built
        assert "python -m applypilot.apply.captcha solve" in built
        assert "browser_evaluate is allowed ONLY" in built
        assert "fill ALL fields in ONE" not in built
        assert "Do NOT try to fill every field on every step in one giant call" in built
        assert "Never use browser_evaluate to set normal form values" in built
        assert "RESULT:CAPTCHA" in built
        assert "RESULT:LOGIN_ISSUE" in built
        # default model is not in prompt, but sonnet is CLI default — CAPTCHA helper is
        assert "CapSolver is NOT CONFIGURED" in built

    def test_dry_run_does_not_submit(self):
        resume_dir = TEST_HOME / "tailored_resumes"
        resume_dir.mkdir(parents=True, exist_ok=True)
        pdf = resume_dir / "job2.pdf"
        pdf.write_bytes(MINIMAL_PDF)
        job = {
            "url": "https://jobs.example.com/2",
            "title": "Engineer",
            "site": "Example",
            "fit_score": 8,
            "location": "Remote",
            "full_description": "Build APIs",
            "tailored_resume_path": str(pdf),
        }
        built = prompt_mod.build_prompt(job, tailored_resume="resume", dry_run=True)
        assert "Do NOT click the final Submit/Apply button" in built


# ---------------------------------------------------------------------------
# manual_ats expansion
# ---------------------------------------------------------------------------

class TestManualATS:
    @pytest.mark.parametrize("url", [
        "https://ibegin.tcsapps.com/apply/1",
        "https://jobs.tcs.com/careers",
        "https://careers.tcs.com/job/123",
        "https://auth.oraclecloud.com/oam/server",
        "https://login.taleo.net/careersection/foo",
    ])
    def test_expanded_manual_ats_domains(self, url):
        assert is_manual_ats(url) is True

    def test_normal_ats_not_manual(self):
        assert is_manual_ats("https://jobs.ashbyhq.com/example/123") is False
        assert is_manual_ats("https://boards.greenhouse.io/example/jobs/1") is False


# ---------------------------------------------------------------------------
# CLI default model
# ---------------------------------------------------------------------------

class TestCLIDefaults:
    def test_apply_help_defaults_to_sonnet(self):
        result = subprocess.run(
            ["applypilot", "apply", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "[default: sonnet]" in result.stdout
        assert "haiku is too weak" in result.stdout


# ---------------------------------------------------------------------------
# Wall-clock timeout in run_job
# ---------------------------------------------------------------------------

class TestKillProcessTreeSafety:
    def test_same_group_child_does_not_kill_parent(self):
        """Regression: killpg on a same-group child used to SIGKILL ApplyPilot."""
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
        )
        try:
            assert os.getpgid(child.pid) == os.getpgrp()
            _kill_process_tree(child.pid)
            child.wait(timeout=5)
            # If killpg hit our group, this assertion never runs.
            assert child.returncode is not None
            assert os.getpid() > 0
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


class TestApplyTimeout:
    def test_hung_claude_is_killed_by_wall_clock(self, monkeypatch):
        resume_dir = TEST_HOME / "tailored_resumes"
        resume_dir.mkdir(parents=True, exist_ok=True)
        pdf = resume_dir / "timeout.pdf"
        txt = resume_dir / "timeout.txt"
        pdf.write_bytes(MINIMAL_PDF)
        txt.write_text("resume", encoding="utf-8")

        bin_dir = Path(tempfile.mkdtemp(prefix="fake-claude-"))
        fake = bin_dir / "claude"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, time\n"
            "sys.stdin.read()\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
        monkeypatch.setitem(config.DEFAULTS, "apply_timeout", 2)

        job = {
            "url": "https://jobs.example.com/timeout",
            "title": "Timeout Role",
            "site": "Example",
            "fit_score": 9,
            "location": "Remote",
            "full_description": "test",
            "tailored_resume_path": str(pdf),
        }
        status, duration_ms = run_job(job, port=9333, worker_id=9, model="sonnet")
        assert status == "failed:timeout"
        assert duration_ms < 15_000


# ---------------------------------------------------------------------------
# Playwright: production DETECT_JS + inject_js against widget HTML
# ---------------------------------------------------------------------------

def _eval_detect(page, html: str):
    page.set_content(html)
    return page.evaluate(captcha.DETECT_JS)


class TestCaptchaBrowserJS:
    @pytest.fixture(scope="class")
    def page(self, request):
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()
        pw.stop()

    def test_detect_hcaptcha(self, page):
        html = '<div class="h-captcha" data-sitekey="hc-key-1"></div>'
        result = _eval_detect(page, html)
        assert result["type"] == "hcaptcha"
        assert result["sitekey"] == "hc-key-1"

    def test_detect_recaptcha_v2(self, page):
        html = '<div class="g-recaptcha" data-sitekey="rc2-key"></div>'
        result = _eval_detect(page, html)
        assert result["type"] == "recaptchav2"
        assert result["sitekey"] == "rc2-key"

    def test_detect_recaptcha_v3_render_param(self, page):
        html = '<script src="https://www.google.com/recaptcha/api.js?render=rc3-sitekey"></script>'
        result = _eval_detect(page, html)
        assert result["type"] == "recaptchav3"
        assert result["sitekey"] == "rc3-sitekey"

    def test_detect_turnstile(self, page):
        html = '<div class="cf-turnstile" data-sitekey="cf-key" data-action="submit"></div>'
        result = _eval_detect(page, html)
        assert result["type"] == "turnstile"
        assert result["sitekey"] == "cf-key"
        assert result["action"] == "submit"

    def test_detect_funcaptcha(self, page):
        html = '<div id="FunCaptcha" data-pkey="fc-key"></div>'
        result = _eval_detect(page, html)
        assert result["type"] == "funcaptcha"
        assert result["sitekey"] == "fc-key"

    def test_detect_none_on_plain_form(self, page):
        html = '<form><input name="first_name"><button>Apply</button></form>'
        result = _eval_detect(page, html)
        assert result is None

    def test_inject_hcaptcha_token(self, page):
        page.set_content('<textarea name="h-captcha-response"></textarea>')
        js = captcha.build_inject_js("hcaptcha", "TOKEN-HC")
        assert page.evaluate(js) == "injected"
        assert page.eval_on_selector('[name="h-captcha-response"]', "el => el.value") == "TOKEN-HC"

    def test_inject_recaptcha_token(self, page):
        page.set_content('<textarea name="g-recaptcha-response"></textarea>')
        js = captcha.build_inject_js("recaptchav2", "TOKEN-RC")
        assert page.evaluate(js) == "injected"
        assert page.eval_on_selector('[name="g-recaptcha-response"]', "el => el.value") == "TOKEN-RC"

    def test_inject_turnstile_token(self, page):
        page.set_content('<input name="cf-turnstile-response">')
        js = captcha.build_inject_js("turnstile", "TOKEN-CF")
        assert page.evaluate(js) == "injected"
        assert page.eval_on_selector('[name="cf-turnstile-response"]', "el => el.value") == "TOKEN-CF"


# ---------------------------------------------------------------------------
# gen_prompt wiring
# ---------------------------------------------------------------------------

class TestGenPrompt:
    def test_gen_prompt_writes_file_and_releases_lock(self):
        db = TEST_HOME / "applypilot.db"
        if db.exists():
            db.unlink()
        # reset thread-local connection
        from applypilot import database
        database.close_connection()
        init_db()
        resume_dir = TEST_HOME / "tailored_resumes"
        resume_dir.mkdir(parents=True, exist_ok=True)
        pdf = resume_dir / "gen.pdf"
        txt = resume_dir / "gen.txt"
        pdf.write_bytes(MINIMAL_PDF)
        txt.write_text("tailored resume text", encoding="utf-8")
        conn = sqlite3.connect(db)
        conn.execute(
            """INSERT INTO jobs (url, title, site, application_url, tailored_resume_path,
               fit_score, location, full_description)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                "https://jobs.example.com/gen",
                "Staff Engineer",
                "ExampleCorp",
                "https://jobs.example.com/gen/apply",
                str(pdf),
                9,
                "Hoboken, NJ",
                JOB_DESCRIPTION,
            ),
        )
        conn.commit()
        conn.close()
        database.close_connection()
        init_db()

        path = gen_prompt("https://jobs.example.com/gen")
        assert path is not None
        text = path.read_text(encoding="utf-8")
        assert "Staff Engineer" in text
        assert "Kubernetes platform" in text
        assert "python -m applypilot.apply.captcha solve" in text
        # lock released
        conn = sqlite3.connect(db)
        status = conn.execute("SELECT apply_status FROM jobs").fetchone()[0]
        conn.close()
        assert status is None
