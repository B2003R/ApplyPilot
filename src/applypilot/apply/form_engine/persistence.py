"""Persist application runs, field events, and submission evidence."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from applypilot.apply.form_engine.models import SubmissionEvidence, utc_now_iso
from applypilot.database import ensure_form_engine_tables, get_connection


class RunStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = get_connection(self.db_path)
        ensure_form_engine_tables(conn)
        return conn

    def create_run(
        self,
        run_id: str,
        *,
        job_url: str | None = None,
        job_title: str | None = None,
        company: str | None = None,
        status: str = "queued",
    ) -> None:
        conn = self._conn()
        now = utc_now_iso()
        conn.execute(
            """
            INSERT OR REPLACE INTO application_runs (
                run_id, job_url, job_title, company, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, job_url, job_title, company, status, now, now),
        )
        conn.commit()

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        conn = self._conn()
        fields["updated_at"] = utc_now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE application_runs SET {cols} WHERE run_id=?",
            (*fields.values(), run_id),
        )
        # Link job row when possible
        if "status" in fields and fields.get("job_url"):
            pass
        conn.commit()

        job_url = fields.get("job_url")
        if job_url is None:
            row = conn.execute(
                "SELECT job_url FROM application_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            job_url = row["job_url"] if row else None
        if job_url:
            conn.execute(
                "UPDATE jobs SET form_engine_run_id=? WHERE url=? OR application_url=?",
                (run_id, job_url, job_url),
            )
            conn.commit()

    def add_field_event(
        self,
        run_id: str,
        *,
        field_signature: str | None = None,
        field_id: str | None = None,
        category: str | None = None,
        action_type: str | None = None,
        outcome: str | None = None,
        confidence: float | None = None,
        value_source: str | None = None,
        unresolved_reason: str | None = None,
        requires_review: bool = False,
    ) -> None:
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO application_field_events (
                run_id, field_signature, field_id, category, action_type, outcome,
                confidence, value_source, unresolved_reason, requires_review, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                field_signature,
                field_id,
                category,
                action_type,
                outcome,
                confidence,
                value_source,
                unresolved_reason,
                1 if requires_review else 0,
                utc_now_iso(),
            ),
        )
        conn.commit()

    def add_submission_evidence(self, run_id: str, evidence: SubmissionEvidence) -> None:
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO application_submission_evidence (
                run_id, result, confirmation_text, confirmation_url,
                confirmation_id, evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                evidence.result,
                evidence.confirmation_text,
                evidence.confirmation_url,
                evidence.confirmation_id,
                json.dumps({"details": evidence.details}),
                utc_now_iso(),
            ),
        )
        conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM application_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None
