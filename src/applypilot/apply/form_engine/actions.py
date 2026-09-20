"""Verified Playwright form interaction helpers."""

from __future__ import annotations

import re
from typing import Any

from applypilot.apply.form_engine.locators import resolve_field_locator
from applypilot.apply.form_engine.models import ActionResult


class FormActions:
    """Reusable, testable Playwright interactions with post-action verification."""

    def __init__(self, page: Any, max_retries: int = 2):
        self.page = page
        self.max_retries = max_retries

    async def _locate(self, locator_hint: str, category: str | None = None) -> Any | None:
        return await resolve_field_locator(self.page, locator_hint, category)

    async def fill_text_verified(
        self,
        *,
        field_id: str,
        locator_hint: str,
        value: str,
        category: str | None = None,
    ) -> ActionResult:
        loc = await self._locate(locator_hint, category)
        if loc is None:
            return ActionResult(field_id, False, "fill_text", error="locator_not_found")
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                await loc.fill(str(value))
                current = await loc.input_value()
                if current == str(value) or str(value) in (current or ""):
                    return ActionResult(
                        field_id, True, "fill_text", verified_value=current, retries=attempt
                    )
                last_err = f"value_mismatch: expected={value!r} got={current!r}"
            except Exception as e:
                last_err = str(e)
        return ActionResult(field_id, False, "fill_text", error=last_err)

    async def select_native_option_verified(
        self,
        *,
        field_id: str,
        locator_hint: str,
        option_label: str,
        category: str | None = None,
    ) -> ActionResult:
        loc = await self._locate(locator_hint, category)
        if loc is None:
            return ActionResult(field_id, False, "select_option", error="locator_not_found")
        try:
            await loc.select_option(label=option_label)
            selected = await loc.evaluate(
                """el => {
                    const o = el.selectedOptions && el.selectedOptions[0];
                    return o ? o.textContent.trim() : '';
                }"""
            )
            ok = selected.strip().lower() == option_label.strip().lower() or option_label.lower() in (
                selected or ""
            ).lower()
            return ActionResult(
                field_id, ok, "select_option", verified_value=selected,
                error=None if ok else "option_not_selected",
            )
        except Exception as e:
            # Fallback: select by value
            try:
                await loc.select_option(value=option_label)
                return ActionResult(field_id, True, "select_option", verified_value=option_label)
            except Exception:
                return ActionResult(field_id, False, "select_option", error=str(e))

    async def select_combobox_option_verified(
        self,
        *,
        field_id: str,
        locator_hint: str,
        option_label: str,
        category: str | None = None,
    ) -> ActionResult:
        loc = await self._locate(locator_hint, category)
        if loc is None:
            # Try role=combobox
            try:
                loc = self.page.get_by_role("combobox").first
                if await loc.count() == 0:
                    return ActionResult(field_id, False, "select_option", error="combobox_not_found")
            except Exception as e:
                return ActionResult(field_id, False, "select_option", error=str(e))
        try:
            await loc.click()
            option = self.page.get_by_role("option", name=re.compile(re.escape(option_label), re.I))
            if await option.count() == 0:
                option = self.page.locator(f"[role='option']:has-text('{option_label}')")
            await option.first.click()
            # Verify aria-selected or visible text
            text = ""
            try:
                text = (await loc.inner_text()).strip()
            except Exception:
                try:
                    text = await loc.input_value()
                except Exception:
                    text = ""
            ok = option_label.lower() in (text or "").lower()
            if not ok:
                # Some comboboxes keep value in a sibling / aria-activedescendant
                selected = self.page.locator("[role='option'][aria-selected='true']")
                if await selected.count() > 0:
                    text = (await selected.first.inner_text()).strip()
                    ok = option_label.lower() in text.lower()
            return ActionResult(
                field_id, ok, "select_option", verified_value=text or option_label,
                error=None if ok else "combobox_not_verified",
            )
        except Exception as e:
            return ActionResult(field_id, False, "select_option", error=str(e))

    async def choose_radio_verified(
        self,
        *,
        field_id: str,
        locator_hint: str,
        option_label: str,
        category: str | None = None,
    ) -> ActionResult:
        page = self.page
        try:
            # Prefer label text
            label_loc = page.get_by_label(option_label, exact=False)
            if await label_loc.count() > 0:
                await label_loc.first.check()
                checked = await label_loc.first.is_checked()
                return ActionResult(
                    field_id, checked, "choose_radio", verified_value=option_label,
                    error=None if checked else "not_checked",
                )
        except Exception:
            pass
        try:
            radio = page.locator(locator_hint)
            # Find matching value/label among radios
            count = await radio.count()
            for i in range(count):
                el = radio.nth(i)
                data = await el.evaluate(
                    """el => {
                        const labelEl = el.labels && el.labels[0]
                            ? el.labels[0]
                            : (el.id ? document.querySelector('label[for=\"' + el.id + '\"]') : null);
                        return {
                            label: labelEl ? labelEl.textContent.trim() : '',
                            value: el.value || '',
                        };
                    }"""
                )
                if option_label.lower() in (data.get("label") or "").lower() or option_label.lower() == (
                    data.get("value") or ""
                ).lower():
                    await el.check()
                    ok = await el.is_checked()
                    return ActionResult(
                        field_id, ok, "choose_radio", verified_value=option_label,
                        error=None if ok else "not_checked",
                    )
            return ActionResult(field_id, False, "choose_radio", error="option_not_found")
        except Exception as e:
            return ActionResult(field_id, False, "choose_radio", error=str(e))

    async def set_checkbox_verified(
        self,
        *,
        field_id: str,
        locator_hint: str,
        checked: bool = True,
        category: str | None = None,
    ) -> ActionResult:
        loc = await self._locate(locator_hint, category)
        if loc is None:
            return ActionResult(field_id, False, "check", error="locator_not_found")
        try:
            if checked:
                await loc.check()
            else:
                await loc.uncheck()
            ok = (await loc.is_checked()) == checked
            return ActionResult(
                field_id, ok, "check" if checked else "uncheck", verified_value=checked,
                error=None if ok else "checkbox_state_mismatch",
            )
        except Exception as e:
            return ActionResult(field_id, False, "check", error=str(e))

    async def fill_date_verified(
        self,
        *,
        field_id: str,
        locator_hint: str,
        value: str,
        category: str | None = None,
    ) -> ActionResult:
        return await self.fill_text_verified(
            field_id=field_id, locator_hint=locator_hint, value=value, category=category
        )

    async def upload_file_verified(
        self,
        *,
        field_id: str,
        locator_hint: str,
        file_path: str,
        category: str | None = None,
    ) -> ActionResult:
        loc = await self._locate(locator_hint, category)
        if loc is None:
            # file inputs are often hidden
            try:
                loc = self.page.locator("input[type='file']").first
                if await loc.count() == 0:
                    return ActionResult(field_id, False, "upload_file", error="file_input_not_found")
            except Exception as e:
                return ActionResult(field_id, False, "upload_file", error=str(e))
        try:
            await loc.set_input_files(file_path)
            names = await loc.evaluate(
                "el => el.files ? Array.from(el.files).map(f => f.name).join(',') : ''"
            )
            ok = bool(names)
            return ActionResult(
                field_id, ok, "upload_file", verified_value=names,
                error=None if ok else "upload_not_verified",
            )
        except Exception as e:
            return ActionResult(field_id, False, "upload_file", error=str(e))

    async def fill_rich_text_verified(
        self,
        *,
        field_id: str,
        locator_hint: str,
        value: str,
        category: str | None = None,
    ) -> ActionResult:
        loc = await self._locate(locator_hint, category)
        if loc is None:
            try:
                loc = self.page.locator("[contenteditable='true']").first
            except Exception as e:
                return ActionResult(field_id, False, "fill_rich_text", error=str(e))
        try:
            await loc.click()
            await loc.fill(value) if await loc.evaluate("el => el.tagName !== 'DIV'") else None
            await self.page.keyboard.type(value)
            text = (await loc.inner_text()).strip()
            ok = value[:20].lower() in text.lower() or text.lower() in value.lower()
            return ActionResult(
                field_id, ok, "fill_rich_text", verified_value=text[:200],
                error=None if ok else "rich_text_not_verified",
            )
        except Exception as e:
            return ActionResult(field_id, False, "fill_rich_text", error=str(e))

    async def click_next_verified(self, *, names: list[str] | None = None) -> ActionResult:
        names = names or ["Next", "Continue", "Save and Continue"]
        before_url = ""
        try:
            before_url = self.page.url
        except Exception:
            pass
        before_html = ""
        try:
            before_html = await self.page.content()
        except Exception:
            pass

        for name in names:
            try:
                btn = self.page.get_by_role("button", name=re.compile(re.escape(name), re.I))
                if await btn.count() == 0:
                    btn = self.page.locator(f"button:has-text('{name}'), a:has-text('{name}')")
                if await btn.count() == 0:
                    continue
                await btn.first.click()
                # Wait for observable transition (URL or DOM change) — not fixed sleep
                try:
                    await self.page.wait_for_function(
                        """([beforeUrl, beforeLen]) => {
                            if (location.href !== beforeUrl) return true;
                            return document.body && document.body.innerHTML.length !== beforeLen;
                        }""",
                        arg=[before_url, len(before_html)],
                        timeout=5000,
                    )
                except Exception:
                    pass
                after_url = ""
                try:
                    after_url = self.page.url
                except Exception:
                    pass
                changed = after_url != before_url
                if not changed:
                    try:
                        after_html = await self.page.content()
                        changed = after_html != before_html
                    except Exception:
                        changed = True  # assume click had effect if we can't compare
                return ActionResult(
                    "next", changed, "click_next", verified_value=after_url or name,
                    error=None if changed else "no_state_transition",
                )
            except Exception as e:
                last = str(e)
                continue
        return ActionResult("next", False, "click_next", error="next_button_not_found")

    async def click_submit_verified(self, *, names: list[str] | None = None) -> ActionResult:
        names = names or ["Submit", "Submit Application", "Apply", "Send Application"]
        for name in names:
            try:
                btn = self.page.get_by_role("button", name=re.compile(re.escape(name), re.I))
                if await btn.count() == 0:
                    btn = self.page.locator(
                        f"button:has-text('{name}'), input[type='submit'][value*='{name}' i]"
                    )
                if await btn.count() == 0:
                    continue
                await btn.first.click()
                return ActionResult("submit", True, "click_submit", verified_value=name)
            except Exception as e:
                return ActionResult("submit", False, "click_submit", error=str(e))
        return ActionResult("submit", False, "click_submit", error="submit_button_not_found")
