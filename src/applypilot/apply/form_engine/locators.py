"""Reusable Playwright locator ladders (no brittle nth-child / absolute XPath)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Awaitable


@dataclass(frozen=True)
class LocatorStrategy:
    """One step in a locator ladder."""

    kind: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] | None = None
    description: str = ""

    @staticmethod
    def by_label(text: str, **kwargs: Any) -> LocatorStrategy:
        return LocatorStrategy("get_by_label", (text,), kwargs or {}, f"label={text!r}")

    @staticmethod
    def by_role(role: str, *, name: str | None = None, **kwargs: Any) -> LocatorStrategy:
        kw = dict(kwargs)
        if name is not None:
            kw["name"] = name
        return LocatorStrategy("get_by_role", (role,), kw, f"role={role!r} name={name!r}")

    @staticmethod
    def by_css(selector: str) -> LocatorStrategy:
        return LocatorStrategy("locator", (selector,), {}, f"css={selector!r}")

    @staticmethod
    def by_placeholder(text: str, **kwargs: Any) -> LocatorStrategy:
        return LocatorStrategy(
            "get_by_placeholder", (text,), kwargs or {}, f"placeholder={text!r}"
        )

    @staticmethod
    def by_testid(test_id: str) -> LocatorStrategy:
        return LocatorStrategy(
            "get_by_test_id", (test_id,), {}, f"testid={test_id!r}"
        )


# Canonical ladders for common identity fields
EMAIL_LOCATOR_LADDER: list[LocatorStrategy] = [
    LocatorStrategy.by_label("Email"),
    LocatorStrategy.by_role("textbox", name="Email"),
    LocatorStrategy.by_css("[autocomplete='email']"),
    LocatorStrategy.by_css("input[type='email']"),
    LocatorStrategy.by_css("input[name*='email' i]"),
]

FIRST_NAME_LOCATOR_LADDER: list[LocatorStrategy] = [
    LocatorStrategy.by_label("First Name"),
    LocatorStrategy.by_label("First name"),
    LocatorStrategy.by_role("textbox", name="First Name"),
    LocatorStrategy.by_css("[autocomplete='given-name']"),
    LocatorStrategy.by_css("input[name*='first' i][name*='name' i]"),
]

LAST_NAME_LOCATOR_LADDER: list[LocatorStrategy] = [
    LocatorStrategy.by_label("Last Name"),
    LocatorStrategy.by_label("Last name"),
    LocatorStrategy.by_role("textbox", name="Last Name"),
    LocatorStrategy.by_css("[autocomplete='family-name']"),
    LocatorStrategy.by_css("input[name*='last' i][name*='name' i]"),
]

PHONE_LOCATOR_LADDER: list[LocatorStrategy] = [
    LocatorStrategy.by_label("Phone"),
    LocatorStrategy.by_label("Phone Number"),
    LocatorStrategy.by_role("textbox", name="Phone"),
    LocatorStrategy.by_css("[autocomplete='tel']"),
    LocatorStrategy.by_css("input[type='tel']"),
    LocatorStrategy.by_css("input[name*='phone' i]"),
]

CATEGORY_LADDERS: dict[str, list[LocatorStrategy]] = {
    "email": EMAIL_LOCATOR_LADDER,
    "first_name": FIRST_NAME_LOCATOR_LADDER,
    "last_name": LAST_NAME_LOCATOR_LADDER,
    "phone": PHONE_LOCATOR_LADDER,
}


async def resolve_locator(page: Any, strategies: list[LocatorStrategy]) -> Any | None:
    """Try each strategy until a visible enabled locator is found.

    Returns a Playwright Locator or None. Uses auto-waiting count/visibility
    checks rather than fixed sleeps.
    """
    for strategy in strategies:
        try:
            method = getattr(page, strategy.kind)
            locator = method(*strategy.args, **(strategy.kwargs or {}))
            # Prefer first match that is visible
            count = await locator.count()
            if count == 0:
                continue
            candidate = locator.first
            if await candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


async def resolve_field_locator(page: Any, locator_hint: str, category: str | None = None) -> Any | None:
    """Resolve a field using hint CSS/role plus optional category ladder."""
    # Direct hint first (stable name/id/css from scanner)
    if locator_hint:
        try:
            loc = page.locator(locator_hint)
            if await loc.count() > 0 and await loc.first.is_visible():
                return loc.first
        except Exception:
            pass

    if category and category in CATEGORY_LADDERS:
        return await resolve_locator(page, CATEGORY_LADDERS[category])
    return None
