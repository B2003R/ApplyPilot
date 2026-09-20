# Form Engine (opt-in)

ATS-aware form filling for ApplyPilot using Playwright attached to the existing
Chrome worker via CDP. **Disabled by default** — the Claude Code + Playwright MCP
apply path is unchanged unless you opt in.

## Enable

Package defaults live in `src/applypilot/config/application_engine.yaml`.
Override locally with `~/.applypilot/application_engine.yaml`:

```yaml
application_engine:
  enabled: true
  auto_submit: false
  use_llm_fallback: true
  diagnostic_mode: true
  trace_on_failure: true
  screenshot_on_failure: true
  adapter_threshold: 0.85
  generic_autofill_threshold: 0.95
  llm_classification_threshold: 0.60
  max_action_retries: 2
  max_state_transitions: 30
  allow_narrative_auto_fill: false
  require_review_for_sensitive_fields: true
  require_review_for_salary: true
```

Or pass CLI flags (do not change the default when omitted):

```bash
applypilot apply --form-engine --dry-run
applypilot apply --no-form-engine
```

## Behavior

1. `chrome.launch_chrome` still owns browser startup and worker profiles.
2. Form engine connects with `playwright.chromium.connect_over_cdp`.
3. Detect ATS → scan Form IR → adapter/semantic fill → verify → rescan.
4. LLM fallback returns structured JSON only (never unrestricted browser actions).
5. CAPTCHA / MFA / OTP → `manual_handoff_required` (no bypass).
6. Submit only when `auto_submit: true` and validation gates pass.
7. Runs/events/mappings/evidence persist in SQLite tables created by `init_db`.

## Diagnostics (local)

When `diagnostic_mode` is true (or on failure with trace/screenshot flags):

- Output under `~/.applypilot/form-engine-diagnostics/<run_id>/`
- `transitions.jsonl`, `field_events.jsonl`, `summary.json`
- Optional `trace.zip` (Playwright trace) and screenshots

Inspect a trace locally:

```bash
npx playwright show-trace ~/.applypilot/form-engine-diagnostics/<run_id>/trace.zip
```

Paths are local only — nothing is uploaded.

## Adapter support

| ATS | Fixture-tested | Notes |
|-----|----------------|-------|
| Greenhouse | Detection, scan, fill, submit controls | Not live-site validated |
| Lever | Detection, scan, submit controls | Not live-site validated |
| Ashby | Detection, state, scan | Initial framework only |
| Workday | Detection, contact step, combobox | Initial framework only |
| Generic | Semantic matching fallback | Used when confidence &lt; threshold |

## Local checklist

1. Configure `~/.applypilot/profile.json` (never commit real PII).
2. Set `application_engine.enabled: true` and `diagnostic_mode: true`.
3. Start apply with an existing Chrome worker (`applypilot apply --form-engine --dry-run`).
4. Prefer a sandbox/test HTML form or staging ATS — do not submit live apps until you review diagnostics.
5. Confirm Form IR, field events, and optional trace/screenshots under the diagnostics directory.
6. Keep `auto_submit: false` until confirmation detection looks correct.

## Tests

```bash
pip install -e ".[dev]"
playwright install chromium
pytest tests/form_engine -v
```

Cloud/CI uses fixtures and mocks only — no live ATS portals.
