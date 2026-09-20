"""SQLite-backed learned field mappings."""

from __future__ import annotations

import sqlite3
from typing import Any

from applypilot.apply.form_engine.models import (
    FieldCategory,
    LearnedFieldMapping,
    utc_now_iso,
)
from applypilot.apply.form_engine.policy import EXACT_ANSWER_CATEGORIES
from applypilot.database import get_connection, ensure_form_engine_tables

# Promote from company → ats after this many successful uses
PROMOTE_TO_ATS_AFTER = 3
# Promote from ats → global after this many
PROMOTE_TO_GLOBAL_AFTER = 5


def _row_to_mapping(row: sqlite3.Row | tuple) -> LearnedFieldMapping:
    if isinstance(row, sqlite3.Row):
        d = dict(row)
    else:
        # fallback positional — unused
        d = {}
    return LearnedFieldMapping(
        ats_name=d.get("ats_name"),
        company_domain=d.get("company_domain"),
        field_signature=d["field_signature"],
        field_category=FieldCategory(d["field_category"]),
        profile_path=d.get("profile_path"),
        scope=d["scope"],
        confidence=float(d.get("confidence") or 0),
        successful_uses=int(d.get("successful_uses") or 0),
        user_confirmed=bool(d.get("user_confirmed")),
        job_url=d.get("job_url"),
    )


class LearningStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = get_connection(self.db_path)
        ensure_form_engine_tables(conn)
        return conn

    def upsert_mapping(self, mapping: LearnedFieldMapping) -> None:
        """Insert or update a mapping. New recoveries start at job/company scope."""
        conn = self._conn()
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO learned_field_mappings (
                ats_name, company_domain, job_url, field_signature, field_category,
                profile_path, scope, confidence, successful_uses, user_confirmed,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, ats_name, company_domain, job_url, field_signature)
            DO UPDATE SET
                field_category=excluded.field_category,
                profile_path=excluded.profile_path,
                confidence=excluded.confidence,
                successful_uses=excluded.successful_uses,
                user_confirmed=excluded.user_confirmed,
                updated_at=excluded.updated_at
            """,
            (
                mapping.ats_name,
                mapping.company_domain,
                mapping.job_url,
                mapping.field_signature,
                mapping.field_category.value,
                mapping.profile_path,
                mapping.scope,
                mapping.confidence,
                mapping.successful_uses,
                1 if mapping.user_confirmed else 0,
                now,
                now,
            ),
        )
        conn.commit()

    def record_success(self, mapping: LearnedFieldMapping) -> LearnedFieldMapping:
        """Increment successful_uses and optionally promote scope (never for sensitive)."""
        mapping.successful_uses += 1
        if mapping.field_category not in EXACT_ANSWER_CATEGORIES:
            if (
                mapping.scope in {"job", "company"}
                and mapping.successful_uses >= PROMOTE_TO_ATS_AFTER
                and mapping.ats_name
            ):
                mapping.scope = "ats"
                mapping.job_url = None
            elif (
                mapping.scope == "ats"
                and mapping.successful_uses >= PROMOTE_TO_GLOBAL_AFTER
            ):
                mapping.scope = "global"
                mapping.company_domain = None
                mapping.job_url = None
        # Sensitive mappings never auto-promote beyond company
        if mapping.field_category in EXACT_ANSWER_CATEGORIES and mapping.scope in {
            "ats",
            "global",
        }:
            mapping.scope = "company"
        self.upsert_mapping(mapping)
        return mapping

    def find_mapping(
        self,
        field_signature: str,
        *,
        ats_name: str | None = None,
        company_domain: str | None = None,
        job_url: str | None = None,
    ) -> LearnedFieldMapping | None:
        """Lookup most specific mapping: job → company → ats → global."""
        conn = self._conn()
        queries = [
            (
                "scope='job' AND job_url=? AND field_signature=?",
                (job_url, field_signature),
            ),
            (
                "scope='company' AND company_domain=? AND field_signature=?",
                (company_domain, field_signature),
            ),
            (
                "scope='ats' AND ats_name=? AND field_signature=?",
                (ats_name, field_signature),
            ),
            ("scope='global' AND field_signature=?", (field_signature,)),
        ]
        for where, params in queries:
            if any(p is None for p in params[:-1]):
                continue
            row = conn.execute(
                f"SELECT * FROM learned_field_mappings WHERE {where} "
                "ORDER BY confidence DESC, successful_uses DESC LIMIT 1",
                params,
            ).fetchone()
            if row:
                return _row_to_mapping(row)
        return None

    def list_mappings(self, limit: int = 100) -> list[LearnedFieldMapping]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM learned_field_mappings ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_mapping(r) for r in rows]
