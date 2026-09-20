"""Generic Form IR scanner — extracts normalized fields from a page/DOM."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from applypilot.apply.form_engine.models import (
    ApplicationState,
    FieldCategory,
    FormField,
    FormIR,
    FormOption,
    SENSITIVE_CATEGORIES,
    WidgetType,
)

# Heuristic tokens for narrative / sensitive detection
_NARRATIVE_RE = re.compile(
    r"why\s+(do\s+you|us|this)|tell\s+us\s+about|cover\s+letter|"
    r"additional\s+information|describe\s+your|what\s+interests",
    re.I,
)
_SENSITIVE_RE = re.compile(
    r"work\s+authori[sz]|sponsor|citizenship|clearance|gender|race|"
    r"ethnicity|veteran|disability|background\s+check|criminal|"
    r"non-?compete|eeo|demographic|salary|compensation|legally\s+authorized",
    re.I,
)


def _widget_from_element(tag: str, input_type: str | None, role: str | None) -> WidgetType:
    tag = (tag or "").lower()
    t = (input_type or "").lower()
    role = (role or "").lower()
    if tag == "textarea":
        return WidgetType.TEXTAREA
    if tag == "select":
        return WidgetType.SELECT
    if role == "combobox" or t == "combobox":
        return WidgetType.COMBOBOX
    if t in {"radio"} or role == "radio":
        return WidgetType.RADIO_GROUP
    if t in {"checkbox"} or role == "checkbox":
        return WidgetType.CHECKBOX
    if t == "email":
        return WidgetType.EMAIL
    if t in {"tel", "phone"}:
        return WidgetType.PHONE
    if t == "url":
        return WidgetType.URL
    if t == "number":
        return WidgetType.NUMBER
    if t == "date":
        return WidgetType.DATE
    if t == "file":
        return WidgetType.FILE
    if t in {"submit", "button"} or tag == "button":
        return WidgetType.BUTTON
    if t in {"text", "search", ""} and tag == "input":
        return WidgetType.TEXT
    if role == "textbox":
        return WidgetType.TEXT
    return WidgetType.UNKNOWN


def field_signature(field: FormField) -> str:
    """Normalized signature for learning — never includes raw applicant values."""
    parts = [
        (field.name or "").lower().strip(),
        (field.element_id or "").lower().strip(),
        (field.autocomplete or "").lower().strip(),
        (field.label or field.aria_label or "").lower().strip()[:80],
        field.widget_type.value,
    ]
    return "|".join(parts)


def _is_narrative(label: str | None, nearby: str | None) -> bool:
    blob = f"{label or ''} {nearby or ''}"
    return bool(_NARRATIVE_RE.search(blob))


def _is_sensitive(label: str | None, nearby: str | None, category: FieldCategory | None = None) -> bool:
    if category and category in SENSITIVE_CATEGORIES:
        return True
    blob = f"{label or ''} {nearby or ''}"
    return bool(_SENSITIVE_RE.search(blob))


async def _scan_options(element: Any) -> list[FormOption]:
    options: list[FormOption] = []
    try:
        tag = await element.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            raw = await element.evaluate(
                """el => Array.from(el.options).map(o => ({
                    label: o.textContent.trim(),
                    value: o.value,
                    selected: o.selected
                }))"""
            )
            for item in raw or []:
                options.append(
                    FormOption(
                        label=item.get("label") or "",
                        value=item.get("value"),
                        selected=bool(item.get("selected")),
                    )
                )
        else:
            # Radios sharing name are collected by the caller; single option here
            pass
    except Exception:
        pass
    return options


async def scan_form(
    page: Any,
    *,
    ats_name: str = "generic",
    application_state: ApplicationState = ApplicationState.APPLICATION_FORM,
    stage_name: str | None = None,
) -> FormIR:
    """Scan the current page into a normalized FormIR.

    Works with Playwright Page/Frame. Does not persist raw field values into
    diagnostics beyond what is needed for verification on the live page object.
    """
    page_url = ""
    try:
        page_url = page.url
    except Exception:
        page_url = ""

    fields: list[FormField] = []
    validation_messages: list[str] = []

    # Collect validation / error text
    try:
        err_loc = page.locator(
            "[role='alert'], .error, .field-error, .validation-error, .invalid-feedback"
        )
        count = await err_loc.count()
        for i in range(min(count, 20)):
            txt = (await err_loc.nth(i).inner_text()).strip()
            if txt:
                validation_messages.append(txt)
    except Exception:
        pass

    # Query interactive controls
    selector = (
        "input:not([type='hidden']):not([type='submit']):not([type='button']), "
        "textarea, select, [role='textbox'], [role='combobox'], "
        "[role='radio'], [role='checkbox'], [contenteditable='true']"
    )
    loc = page.locator(selector)
    try:
        n = await loc.count()
    except Exception:
        n = 0

    seen_radio_names: set[str] = set()

    for i in range(n):
        el = loc.nth(i)
        try:
            meta = await el.evaluate(
                """el => {
                    const labelEl = el.labels && el.labels[0]
                        ? el.labels[0]
                        : (el.id ? document.querySelector('label[for=\"' + el.id + '\"]') : null);
                    const labelledBy = el.getAttribute('aria-labelledby');
                    let ariaLabelledText = null;
                    if (labelledBy) {
                        ariaLabelledText = labelledBy.split(/\\s+/).map(id => {
                            const n = document.getElementById(id);
                            return n ? n.textContent.trim() : '';
                        }).filter(Boolean).join(' ');
                    }
                    const section = el.closest('section, fieldset, [role=\"group\"]');
                    const heading = section
                        ? (section.querySelector('legend, h1, h2, h3, h4') || {}).textContent
                        : null;
                    const nearby = (el.parentElement && el.parentElement.innerText || '').slice(0, 200);
                    return {
                        tag: el.tagName.toLowerCase(),
                        type: el.getAttribute('type'),
                        name: el.getAttribute('name'),
                        id: el.id || null,
                        autocomplete: el.getAttribute('autocomplete'),
                        placeholder: el.getAttribute('placeholder'),
                        ariaLabel: el.getAttribute('aria-label'),
                        ariaLabelledText,
                        role: el.getAttribute('role'),
                        required: el.required || el.getAttribute('aria-required') === 'true',
                        disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
                        value: el.value != null ? String(el.value).slice(0, 200) : null,
                        label: labelEl ? labelEl.textContent.trim() : null,
                        section: heading ? String(heading).trim().slice(0, 120) : null,
                        nearby: nearby,
                        contentEditable: el.isContentEditable,
                    };
                }"""
            )
        except Exception:
            continue

        input_type = meta.get("type")
        name = meta.get("name")
        # Collapse radio groups by name
        if (input_type or "").lower() == "radio" and name:
            if name in seen_radio_names:
                continue
            seen_radio_names.add(name)
            widget = WidgetType.RADIO_GROUP
            options = await _collect_radio_options(page, name)
        else:
            widget = _widget_from_element(meta.get("tag"), input_type, meta.get("role"))
            if meta.get("contentEditable"):
                widget = WidgetType.RICH_TEXT
            options = await _scan_options(el) if widget == WidgetType.SELECT else []

        label = meta.get("label") or meta.get("ariaLabel") or meta.get("ariaLabelledText")
        nearby = meta.get("nearby")
        narrative = _is_narrative(label, nearby) or widget == WidgetType.TEXTAREA and _is_narrative(
            label, nearby
        )
        # Long unlabeled textareas near "why" questions
        if widget == WidgetType.TEXTAREA and _NARRATIVE_RE.search(f"{label or ''} {nearby or ''}"):
            narrative = True

        sensitive = _is_sensitive(label, nearby)
        element_id = meta.get("id")
        field_id = element_id or name or f"field_{i}"
        if widget == WidgetType.RADIO_GROUP and name:
            field_id = f"radio:{name}"

        # Prefer stable locator hints
        if element_id:
            locator_hint = f"#{element_id}"
        elif name and widget != WidgetType.RADIO_GROUP:
            locator_hint = f"[name='{name}']"
        elif name and widget == WidgetType.RADIO_GROUP:
            locator_hint = f"input[type='radio'][name='{name}']"
        else:
            locator_hint = selector  # fallback; executor will use ladders

        # Repeated section heuristic
        repeated = bool(
            re.search(r"(education|experience|employment).*\d+", f"{name or ''}{element_id or ''}", re.I)
            or (meta.get("section") or "").lower() in {"education", "experience", "work history"}
        )

        fields.append(
            FormField(
                field_id=str(field_id),
                locator_hint=locator_hint if locator_hint != selector else (
                    f"#{element_id}" if element_id else f"[name='{name}']" if name else f"xpath=(//input|//textarea|//select)[{i+1}]"
                ),
                label=label,
                aria_label=meta.get("ariaLabel"),
                placeholder=meta.get("placeholder"),
                name=name,
                element_id=element_id,
                autocomplete=meta.get("autocomplete"),
                input_type=input_type,
                widget_type=widget,
                required=bool(meta.get("required")),
                visible=await _safe_visible(el),
                enabled=not bool(meta.get("disabled")),
                current_value=meta.get("value"),
                options=options,
                section=meta.get("section"),
                nearby_text=(nearby or "")[:160] or None,
                is_sensitive=sensitive,
                is_narrative=narrative,
                is_repeated_section_field=repeated,
                frame_path=[],
                metadata={"scan_index": i},
            )
        )

    # Fix bad absolute xpath fallbacks — use nth locator style instead
    for f in fields:
        if f.locator_hint.startswith("xpath="):
            # Prefer name/id; leave as-is only if nothing else — replace with label-based hint
            if f.label:
                f.locator_hint = f"label:{f.label}"
            elif f.placeholder:
                f.locator_hint = f"placeholder:{f.placeholder}"

    submit_present = await _button_present(
        page, ["Submit", "Submit Application", "Apply", "Send Application"]
    )
    next_present = await _button_present(
        page, ["Next", "Continue", "Save and Continue", "Review"]
    )

    return FormIR(
        ats_name=ats_name,
        page_url=page_url,
        application_state=application_state,
        stage_name=stage_name,
        fields=fields,
        submit_button_present=submit_present,
        next_button_present=next_present,
        validation_messages=validation_messages,
        detected_at=datetime.now(timezone.utc),
    )


async def _safe_visible(el: Any) -> bool:
    try:
        return bool(await el.is_visible())
    except Exception:
        return True


async def _collect_radio_options(page: Any, name: str) -> list[FormOption]:
    options: list[FormOption] = []
    loc = page.locator(f"input[type='radio'][name='{name}']")
    try:
        count = await loc.count()
    except Exception:
        return options
    for i in range(count):
        el = loc.nth(i)
        try:
            data = await el.evaluate(
                """el => {
                    const labelEl = el.labels && el.labels[0]
                        ? el.labels[0]
                        : (el.id ? document.querySelector('label[for=\"' + el.id + '\"]') : null);
                    return {
                        label: labelEl ? labelEl.textContent.trim() : (el.value || ''),
                        value: el.value,
                        selected: el.checked,
                    };
                }"""
            )
            options.append(
                FormOption(
                    label=data.get("label") or "",
                    value=data.get("value"),
                    selected=bool(data.get("selected")),
                )
            )
        except Exception:
            continue
    return options


async def _button_present(page: Any, names: list[str]) -> bool:
    for name in names:
        try:
            loc = page.get_by_role("button", name=re.compile(re.escape(name), re.I))
            if await loc.count() > 0:
                return True
        except Exception:
            continue
        try:
            loc = page.locator(
                f"button:has-text('{name}'), input[type='submit'][value*='{name}' i], a:has-text('{name}')"
            )
            if await loc.count() > 0:
                return True
        except Exception:
            continue
    return False


def company_domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        return host.lower() or None
    except Exception:
        return None
