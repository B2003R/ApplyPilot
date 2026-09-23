"""ApplyPilot must survive killing a Claude apply session.

``run_job`` stops a timed-out ``claude`` process with ``killpg``. If that
process shares ApplyPilot's process group, SIGKILL takes down the CLI in the
middle of the application instead of recording ``failed:timeout``.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

_FIXTURE_PROFILE = (
    Path(__file__).parent / "fixtures" / "profiles" / "synthetic_candidate.json"
)


def _write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_run_job_timeout_does_not_sigkill_applypilot(tmp_path: Path) -> None:
    """A hung Claude session is marked failed:timeout; the CLI stays alive."""
    bin_dir = tmp_path / "bin"
    app_dir = tmp_path / "applypilot"
    bin_dir.mkdir()
    app_dir.mkdir()
    shutil.copy(_FIXTURE_PROFILE, app_dir / "profile.json")
    resume_pdf = tmp_path / "resume.pdf"
    resume_pdf.write_bytes(b"%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    _write_executable(
        bin_dir / "claude",
        """\
        #!/usr/bin/env python3
        import json, sys, time
        sys.stdin.read()
        sys.stdout.write(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "filling work history"}]},
        }) + "\\n")
        sys.stdout.flush()
        time.sleep(30)
        """,
    )
    victim = tmp_path / "victim.py"
    victim.write_text(
        textwrap.dedent(
            """\
            import os
            import sys

            os.environ["APPLYPILOT_DIR"] = sys.argv[1]
            from applypilot import config
            from applypilot.apply import dashboard
            from applypilot.apply.launcher import run_job

            config.DEFAULTS["apply_timeout"] = 2
            config.ensure_dirs()
            dashboard.init_worker(0)
            status, duration_ms = run_job(
                {
                    "url": "https://example.com/jobs/1",
                    "title": "Engineer",
                    "site": "example",
                    "application_url": "https://example.com/jobs/1",
                    "fit_score": 9,
                    "tailored_resume_path": sys.argv[2],
                    "cover_letter_path": None,
                    "location": "Remote",
                    "full_description": "Build things.",
                },
                port=9222,
                worker_id=0,
                model="sonnet",
                dry_run=True,
            )
            print(f"STATUS={status}")
            print(f"DURATION_MS={duration_ms}")
            """
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["APPLYPILOT_DIR"] = str(app_dir)
    completed = subprocess.run(
        [sys.executable, str(victim), str(app_dir), str(resume_pdf)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        # Isolate the victim so a buggy killpg cannot SIGKILL pytest.
        start_new_session=True,
    )

    assert completed.returncode == 0, (
        f"apply process died (rc={completed.returncode})\\n"
        f"stdout={completed.stdout}\\nstderr={completed.stderr}"
    )
    assert "STATUS=failed:timeout" in completed.stdout
    assert "DURATION_MS=" in completed.stdout


def test_kill_process_tree_spares_caller_process_group(tmp_path: Path) -> None:
    """killpg must not target the process group ApplyPilot itself belongs to."""
    victim = tmp_path / "kill_victim.py"
    victim.write_text(
        textwrap.dedent(
            """\
            import subprocess
            import time
            from applypilot.apply.chrome import _kill_process_tree

            proc = subprocess.Popen(["sleep", "30"])
            time.sleep(0.2)
            _kill_process_tree(proc.pid)
            print("SURVIVED")
            proc.wait(timeout=5)
            """
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(victim)],
        capture_output=True,
        text=True,
        timeout=15,
        start_new_session=True,
    )
    assert completed.returncode == 0, (
        f"caller died (rc={completed.returncode})\\n"
        f"stdout={completed.stdout}\\nstderr={completed.stderr}"
    )
    assert "SURVIVED" in completed.stdout
