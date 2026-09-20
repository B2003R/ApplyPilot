"""Centralized fill policy for the form engine.

Never invents answers. Sensitive/legal/demographic fields require exact
configured values or local human confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from applypilot.apply.form_engine.models import (
    SENSITIVE_CATEGORIES,
    FieldCategory,
    FieldMatch,
    FormField,
)


# Categories that may be auto-filled when confidence and profile data allow.
AUTO_FILL_CATEGORIES: frozenset[FieldCategory] = frozenset(
    {
        FieldCategory.FIRST_NAME,
        FieldCategory.LAST_NAME,
        FieldCategory.PREFERRED_NAME,
        FieldCategory.EMAIL,
        FieldCategory.PHONE,
        FieldCategory.ADDRESS,
        FieldCategory.CITY,
        FieldCategory.STATE,
        FieldCategory.COUNTRY,
        FieldCategory.POSTAL_CODE,
        FieldCategory.LINKEDIN,
        FieldCategory.GITHUB,
        FieldCategory.PORTFOLIO,
        FieldCategory.EDUCATION,
        FieldCategory.EXPERIENCE,
        FieldCategory.SKILLS,
        FieldCategory.RESUME,
        FieldCategory.COVER_LETTER,
        FieldCategory.AVAILABILITY,
    }
)

EXACT_ANSWER_CATEGORIES: frozenset[FieldCategory] = frozenset(
    {
        FieldCategory.WORK_AUTHORIZATION,
        FieldCategory.SPONSORSHIP,
        FieldCategory.CITIZENSHIP,
        FieldCategory.CLEARANCE,
        FieldCategory.LEGAL,
        FieldCategory.CONSENT,
        FieldCategory.DEMOGRAPHIC,
    }
)


@dataclass
class FillPolicy:
    """Policy knobs governing what may be filled automatically."""

    auto_submit: bool = False
    use_llm_fallback: bool = True
    allow_narrative_auto_fill: bool = False
    require_review_for_sensitive_fields: bool = True
    require_review_for_salary: bool = True
    generic_autofill_threshold: float = 0.95
    llm_classification_threshold: float = 0.60
    # Explicit approvals: category -> approved exact answer (string/bool)
    approved_answers: dict[str, Any] = field(default_factory=dict)
    # Profile paths the policy treats as explicitly approved for sensitive use
    approved_profile_paths: set[str] = field(default_factory=set)

    def is_sensitive(self, category: FieldCategory) -> bool:
        return category in SENSITIVE_CATEGORIES or category in EXACT_ANSWER_CATEGORIES

    def can_auto_fill_category(self, category: FieldCategory) -> bool:
        if category == FieldCategory.NARRATIVE:
            return self.allow_narrative_auto_fill
        if category == FieldCategory.SALARY:
            return (
                not self.require_review_for_salary
                and "salary" in self.approved_answers
            )
        if category in EXACT_ANSWER_CATEGORIES:
            return category.value in self.approved_answers or any(
                p.startswith(category.value) for p in self.approved_profile_paths
            )
        return category in AUTO_FILL_CATEGORIES

    def decision_for_match(
        self,
        match: FieldMatch,
        field: FormField | None = None,
    ) -> str:
        """Return one of: auto_fill, review, llm, unresolved, skip_sensitive.

        Thresholds:
          >= 0.95: auto-fill if policy allows
          0.80–0.94: fill only if policy allows (lower-confidence event)
          0.60–0.79: structured LLM classification
          < 0.60: unresolved for human-assisted local resolution
        Sensitive: never fill from semantic similarity alone.
        """
        if match.is_sensitive or match.category in EXACT_ANSWER_CATEGORIES:
            if self.require_review_for_sensitive_fields:
                if match.category.value in self.approved_answers:
                    return "auto_fill"
                return "skip_sensitive"
            return "skip_sensitive"

        if match.category == FieldCategory.NARRATIVE and not self.allow_narrative_auto_fill:
            return "review"

        if match.category == FieldCategory.SALARY and self.require_review_for_salary:
            if "salary" not in self.approved_answers:
                return "review"

        if not self.can_auto_fill_category(match.category):
            return "review"

        if match.confidence >= self.generic_autofill_threshold:
            return "auto_fill"
        if match.confidence >= 0.80:
            return "auto_fill"  # caller records lower-confidence event
        if match.confidence >= self.llm_classification_threshold:
            return "llm" if self.use_llm_fallback else "unresolved"
        return "unresolved"

    def exact_answer(self, category: FieldCategory) -> Any | None:
        return self.approved_answers.get(category.value)

    @classmethod
    def from_config(
        cls,
        cfg: dict[str, Any],
        *,
        approved_answers: dict[str, Any] | None = None,
    ) -> FillPolicy:
        return cls(
            auto_submit=bool(cfg.get("auto_submit", False)),
            use_llm_fallback=bool(cfg.get("use_llm_fallback", True)),
            allow_narrative_auto_fill=bool(cfg.get("allow_narrative_auto_fill", False)),
            require_review_for_sensitive_fields=bool(
                cfg.get("require_review_for_sensitive_fields", True)
            ),
            require_review_for_salary=bool(cfg.get("require_review_for_salary", True)),
            generic_autofill_threshold=float(cfg.get("generic_autofill_threshold", 0.95)),
            llm_classification_threshold=float(
                cfg.get("llm_classification_threshold", 0.60)
            ),
            approved_answers=dict(approved_answers or {}),
        )
