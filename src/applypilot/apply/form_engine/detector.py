"""Confidence-based ATS detector."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from applypilot.apply.form_engine.models import ATSDetection

# (ats_name, host_substrings, url_regexes, dom_markers, title_regexes)
_SIGNATURES: list[tuple[str, list[str], list[str], list[str], list[str]]] = [
    (
        "greenhouse",
        ["greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io"],
        [r"greenhouse\.io", r"grnhse"],
        ["#application_form", "#submit_app", ".accessible", "[data-field='first_name']", "form#application-form"],
        [r"greenhouse", r"job application"],
    ),
    (
        "lever",
        ["lever.co", "jobs.lever.co"],
        [r"lever\.co"],
        ["[data-qa='btn-submit']", ".application-form", "#resume-upload", "div.application-page"],
        [r"lever", r"jobs at"],
    ),
    (
        "ashby",
        ["ashbyhq.com", "jobs.ashbyhq.com"],
        [r"ashbyhq\.com", r"ashby"],
        ["[data-testid='application-form']", ".ashby-application-form", "#ashby_form"],
        [r"ashby"],
    ),
    (
        "workday",
        ["myworkdayjobs.com", "myworkdaysite.com", "workday.com"],
        [r"myworkdayjobs", r"workday"],
        ["[data-automation-id='applyButton']", "[data-automation-id='jobPostingPage']",
         "[data-automation-id='formField']", "div[data-uxi-widget-type]"],
        [r"workday", r"career"],
    ),
]


async def detect_ats(page: Any, url: str | None = None) -> ATSDetection:
    """Detect ATS with a confidence score in [0, 1]."""
    page_url = url or ""
    try:
        page_url = url or page.url or ""
    except Exception:
        page_url = url or ""

    host = ""
    try:
        host = (urlparse(page_url).hostname or "").lower()
    except Exception:
        host = ""

    title = ""
    try:
        title = (await page.title()) or ""
    except Exception:
        title = ""

    best = ATSDetection(ats_name="generic", confidence=0.0, reason="no signals")

    for ats_name, hosts, url_re, markers, title_re in _SIGNATURES:
        score = 0.0
        reasons: list[str] = []

        if any(h in host for h in hosts):
            score += 0.55
            reasons.append(f"host:{host}")

        for pat in url_re:
            if re.search(pat, page_url, re.I):
                score += 0.2
                reasons.append(f"url:{pat}")
                break

        for pat in title_re:
            if re.search(pat, title, re.I):
                score += 0.1
                reasons.append(f"title:{pat}")
                break

        for marker in markers:
            try:
                loc = page.locator(marker)
                if await loc.count() > 0:
                    score += 0.25
                    reasons.append(f"dom:{marker}")
                    break
            except Exception:
                continue

        # Fixture data attributes
        try:
            if await page.locator(f"[data-ats='{ats_name}']").count() > 0:
                score += 0.5
                reasons.append(f"data-ats={ats_name}")
        except Exception:
            pass

        score = min(score, 1.0)
        if score > best.confidence:
            best = ATSDetection(
                ats_name=ats_name,
                confidence=score,
                reason="; ".join(reasons) if reasons else "weak",
            )

    if best.confidence < 0.35:
        return ATSDetection(ats_name="generic", confidence=max(best.confidence, 0.2), reason=best.reason or "fallback")
    return best
