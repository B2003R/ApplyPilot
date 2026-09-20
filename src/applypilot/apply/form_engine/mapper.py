"""Deterministic semantic field matcher with weighted evidence."""

from __future__ import annotations

import re
from typing import Any

from applypilot.apply.form_engine.models import FieldCategory, FieldMatch, FormField, FormIR
from applypilot.apply.form_engine.policy import EXACT_ANSWER_CATEGORIES, FillPolicy

# (category, profile_path, patterns for label/name/id/autocomplete/placeholder)
_RULES: list[tuple[FieldCategory, str, list[str], dict[str, float]]] = [
    (FieldCategory.EMAIL, "personal.email", [r"e-?mail", r"^email$"], {"autocomplete:email": 0.5, "type:email": 0.35}),
    (FieldCategory.FIRST_NAME, "personal.full_name", [r"first\s*name", r"given\s*name", r"^fname$"], {"autocomplete:given-name": 0.5}),
    (FieldCategory.LAST_NAME, "personal.full_name", [r"last\s*name", r"family\s*name", r"surname", r"^lname$"], {"autocomplete:family-name": 0.5}),
    (FieldCategory.PREFERRED_NAME, "personal.preferred_name", [r"preferred\s*name", r"nickname"], {}),
    (FieldCategory.PHONE, "personal.phone", [r"phone", r"mobile", r"tel"], {"autocomplete:tel": 0.5, "type:tel": 0.35}),
    (FieldCategory.ADDRESS, "personal.address", [r"street\s*address", r"^address$", r"address\s*line"], {"autocomplete:street-address": 0.45}),
    (FieldCategory.CITY, "personal.city", [r"^city$", r"town"], {"autocomplete:address-level2": 0.45}),
    (FieldCategory.STATE, "personal.province_state", [r"state", r"province", r"region"], {"autocomplete:address-level1": 0.45}),
    (FieldCategory.COUNTRY, "personal.country", [r"country"], {"autocomplete:country": 0.45}),
    (FieldCategory.POSTAL_CODE, "personal.postal_code", [r"postal", r"zip"], {"autocomplete:postal-code": 0.45}),
    (FieldCategory.LINKEDIN, "personal.linkedin_url", [r"linkedin"], {}),
    (FieldCategory.GITHUB, "personal.github_url", [r"github"], {}),
    (FieldCategory.PORTFOLIO, "personal.portfolio_url", [r"portfolio", r"website", r"personal\s*site"], {}),
    (FieldCategory.RESUME, "resume", [r"resume", r"cv", r"upload.*resume"], {"type:file": 0.3}),
    (FieldCategory.COVER_LETTER, "cover_letter", [r"cover\s*letter"], {}),
    (FieldCategory.EDUCATION, "experience.education_level", [r"education", r"degree", r"school", r"university"], {}),
    (FieldCategory.EXPERIENCE, "experience.years_of_experience_total", [r"years?\s*(of\s*)?experience", r"work\s*experience"], {}),
    (FieldCategory.SKILLS, "skills_boundary", [r"^skills?$", r"technical\s*skills"], {}),
    (FieldCategory.AVAILABILITY, "availability.earliest_start_date", [r"start\s*date", r"availab", r"notice\s*period"], {}),
    (FieldCategory.WORK_AUTHORIZATION, "work_authorization.legally_authorized_to_work", [r"work\s*authori", r"legally\s*authorized", r"authorized\s*to\s*work"], {}),
    (FieldCategory.SPONSORSHIP, "work_authorization.require_sponsorship", [r"sponsor", r"visa"], {}),
    (FieldCategory.CITIZENSHIP, None, [r"citizen"], {}),
    (FieldCategory.CLEARANCE, None, [r"security\s*clearance", r"clearance"], {}),
    (FieldCategory.SALARY, "compensation.salary_expectation", [r"salary", r"compensation", r"expected\s*pay", r"pay\s*expect"], {}),
    (FieldCategory.DEMOGRAPHIC, "eeo_voluntary", [r"gender", r"race", r"ethnicity", r"veteran", r"disability", r"eeo", r"demographic"], {}),
    (FieldCategory.LEGAL, None, [r"background\s*check", r"criminal", r"felony", r"non-?compete", r"attest"], {}),
    (FieldCategory.CONSENT, None, [r"i\s*agree", r"consent", r"terms\s*and\s*conditions", r"privacy\s*policy"], {}),
    (FieldCategory.NARRATIVE, None, [r"why\s+(do\s+you|us|this)", r"tell\s+us", r"additional\s+information", r"cover\s*letter\s*text"], {}),
]


def _blob(field: FormField) -> str:
    parts = [
        field.label or "",
        field.aria_label or "",
        field.name or "",
        field.element_id or "",
        field.placeholder or "",
        field.nearby_text or "",
        field.section or "",
        field.autocomplete or "",
        field.input_type or "",
    ]
    return " ".join(parts).lower()


def score_field(field: FormField) -> FieldMatch:
    """Score a single field against the ontology; return best FieldMatch."""
    blob = _blob(field)
    best: FieldMatch | None = None

    for category, profile_path, patterns, bonuses in _RULES:
        score = 0.0
        hits: list[str] = []

        for pat in patterns:
            if re.search(pat, blob, re.I):
                score += 0.35
                hits.append(f"pattern:{pat}")
                break

        # Attribute bonuses
        ac = (field.autocomplete or "").lower()
        it = (field.input_type or "").lower()
        for key, weight in bonuses.items():
            kind, val = key.split(":", 1)
            if kind == "autocomplete" and ac == val:
                score += weight
                hits.append(key)
            elif kind == "type" and it == val:
                score += weight
                hits.append(key)

        # Widget type soft signal
        if category == FieldCategory.NARRATIVE and field.widget_type.value in {"textarea", "rich_text"}:
            score += 0.15
            hits.append("widget:textarea")
        if category == FieldCategory.RESUME and field.widget_type.value == "file":
            score += 0.25
            hits.append("widget:file")

        # Explicit flags from scanner
        if field.is_narrative and category == FieldCategory.NARRATIVE:
            score += 0.25
            hits.append("flag:narrative")
        if field.is_sensitive and category in EXACT_ANSWER_CATEGORIES | {
            FieldCategory.DEMOGRAPHIC,
            FieldCategory.SALARY,
        }:
            score += 0.2
            hits.append("flag:sensitive")

        score = min(score, 1.0)
        if score <= 0:
            continue

        is_sensitive = category in EXACT_ANSWER_CATEGORIES or category in {
            FieldCategory.DEMOGRAPHIC,
            FieldCategory.SALARY,
            FieldCategory.CLEARANCE,
            FieldCategory.CITIZENSHIP,
        }
        candidate = FieldMatch(
            field_id=field.field_id,
            category=category,
            profile_path=profile_path,
            confidence=score,
            explanation="; ".join(hits) if hits else "weak signal",
            requires_review=is_sensitive or score < 0.95 or category == FieldCategory.NARRATIVE,
            is_sensitive=is_sensitive or field.is_sensitive,
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate

    if best is None:
        return FieldMatch(
            field_id=field.field_id,
            category=FieldCategory.UNKNOWN,
            profile_path=None,
            confidence=0.0,
            explanation="no semantic signals",
            requires_review=True,
            is_sensitive=field.is_sensitive,
        )
    return best


def match_form(form_ir: FormIR, policy: FillPolicy | None = None) -> list[FieldMatch]:
    """Match all fields in a FormIR."""
    return [score_field(f) for f in form_ir.fields if f.visible]


def resolve_profile_value(profile: dict[str, Any], path: str | None) -> Any | None:
    """Resolve dotted path against a profile dict. Special-cases full_name splits."""
    if not path:
        return None
    if path == "resume":
        return profile.get("_resume_path")
    if path == "cover_letter":
        return profile.get("_cover_letter_path")

    cur: Any = profile
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def first_name_from_profile(profile: dict[str, Any]) -> str | None:
    preferred = (profile.get("personal") or {}).get("preferred_name")
    if preferred:
        return str(preferred).split()[0]
    full = (profile.get("personal") or {}).get("full_name") or ""
    parts = str(full).strip().split()
    return parts[0] if parts else None


def last_name_from_profile(profile: dict[str, Any]) -> str | None:
    full = (profile.get("personal") or {}).get("full_name") or ""
    parts = str(full).strip().split()
    return parts[-1] if len(parts) >= 2 else None


def value_for_match(match: FieldMatch, profile: dict[str, Any]) -> Any | None:
    """Map a FieldMatch to a concrete profile value without inventing facts."""
    if match.category == FieldCategory.FIRST_NAME:
        return first_name_from_profile(profile)
    if match.category == FieldCategory.LAST_NAME:
        return last_name_from_profile(profile)
    if match.category == FieldCategory.SKILLS:
        boundary = profile.get("skills_boundary") or {}
        if isinstance(boundary, dict):
            bits = []
            for items in boundary.values():
                if isinstance(items, list):
                    bits.extend(items)
            return ", ".join(bits) if bits else None
    return resolve_profile_value(profile, match.profile_path)
