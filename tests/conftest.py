"""Shared pytest fixtures for form-engine tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from applypilot.database import close_connection, init_db

FIXTURES = Path(__file__).parent / "fixtures"
FORMS = FIXTURES / "forms"
PROFILES = FIXTURES / "profiles"


@pytest.fixture
def forms_dir() -> Path:
    return FORMS


@pytest.fixture
def synthetic_profile() -> dict:
    return json.loads((PROFILES / "synthetic_candidate.json").read_text(encoding="utf-8"))


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("applypilot.database.DB_PATH", db_path)
    conn = init_db(db_path)
    yield db_path
    close_connection(db_path)


@pytest.fixture
def fixture_html(forms_dir):
    def _load(name: str) -> str:
        return (forms_dir / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture
async def pw_page():
    """Async Playwright page for fixture HTML tests."""
    playwright = None
    browser = None
    try:
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        yield page
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()


@pytest.fixture
def load_fixture_page(pw_page, fixture_html):
    async def _load(name: str):
        html = fixture_html(name)
        await pw_page.set_content(html, wait_until="domcontentloaded")
        # Fake URL for ATS host detection when needed
        await pw_page.evaluate(
            """() => {
              // no-op; URL stays about:blank — detectors use data-ats
            }"""
        )
        return pw_page

    return _load
