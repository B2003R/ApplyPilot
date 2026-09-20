# Form Engine Implementation Plan

ATS-aware, Playwright-based job-application form-filling engine for ApplyPilot.

**Status:** Implementation plan (cloud-safe architecture). Not production-validated against live ATS portals.

---

## 1. Existing integration points

ApplyPilot’s apply stage today is:

1. CLI `applypilot apply` → [`cli.py`](../src/applypilot/cli.py)
2. [`launcher.worker_loop`](../src/applypilot/apply/launcher.py) acquires a job from SQLite
3. [`chrome.launch_chrome`](../src/applypilot/apply/chrome.py) starts an isolated Chrome with CDP
4. [`launcher.run_job`](../src/applypilot/apply/launcher.py) spawns Claude Code with Playwright MCP attached to that CDP port
5. [`prompt.build_prompt`](../src/applypilot/apply/prompt.py) instructs the agent how to fill/submit
6. Result strings (`RESULT:APPLIED`, etc.) update `jobs.apply_status`

**Form-engine hook:** After Chrome is launched in `worker_loop`, if `application_engine.enabled` is true, run the form-engine orchestrator against the same CDP endpoint instead of spawning Claude. Otherwise keep the Claude path unchanged.

Other useful points:

| Module | Role for form engine |
|--------|----------------------|
| `config.load_profile()` | Synthetic/local profile dict for fills |
| `database.init_db` / `get_connection` | Persist runs, events, learned mappings |
| `llm.get_client()` | Constrained structured JSON fallback only |
| `dashboard.update_state` / `add_event` | Progress UX parity |
| `config/sites.yaml` `manual_ats` | Skip known-manual portals (existing behavior) |

---

## 2. Existing browser launch / login ownership

**Browser owner:** [`apply/chrome.py`](../src/applypilot/apply/chrome.py) only.

- Worker profile cloning under `~/.applypilot/chrome-workers/`
- CDP port = `9222 + worker_id`
- Process lifecycle, cleanup, restore-nag suppression

**Login owner:** Today, login is *not* a separate Python module. It is instructed inside the Claude prompt (email/password from profile, CAPTCHA detect → CapSolver helper for the Claude path).

**Form-engine rule:**

- Never launch a second browser or Chrome extension.
- Attach with `playwright.chromium.connect_over_cdp(http://localhost:{port})`.
- On `LOGIN_REQUIRED`: reuse worker session cookies; if still on login → structured `ManualHandoffRequired` mapped to existing `login_issue`.
- On `CAPTCHA_REQUIRED` / `MFA_OR_OTP_REQUIRED`: return manual handoff. Do **not** call CapSolver or bypass MFA from the form engine.

---

## 3. Existing SQLite database patterns

- Single primary table: `jobs` (`url` PRIMARY KEY)
- Schema evolution: `CREATE TABLE IF NOT EXISTS` + `_ALL_COLUMNS` registry + `ALTER TABLE ... ADD COLUMN` in `ensure_columns()`
- Thread-local connections, WAL, `db_path=` override for tests
- Apply markers: `apply_status`, `applied_at`, `apply_error`, `apply_attempts`, `agent_id`, …

**Form-engine persistence (additive):**

| Table | Purpose |
|-------|---------|
| `application_runs` | Per-attempt run metadata / status / ATS detection |
| `application_field_events` | Field actions without raw PII dumps |
| `learned_field_mappings` | Signature → category/profile_path with scopes |
| `application_submission_evidence` | Confirmation evidence / result |

Optional `jobs.form_engine_run_id` column for linkage. Terminal outcomes still map back to existing `apply_status` values so the queue/dashboard keep working.

---

## 4. Existing LLM / Claude abstractions

- [`llm.LLMClient`](../src/applypilot/llm.py) via `get_client()` — Gemini / OpenAI / local
- Callers use `.chat(messages)` / `.ask(prompt)` returning **strings**
- Apply stage Claude Code is a **separate** subprocess (not `LLMClient`)

**Form-engine LLM use:**

- Only for unresolved / narrative / ambiguous fields
- Strict JSON → typed dataclasses (`LLMFieldResolution`)
- Never submit, never unrestricted browser loops, never invent facts, never resolve sensitive categories alone
- Cloud tests mock `get_client()`; no live API calls

---

## 5. Minimal new modules

```text
src/applypilot/apply/form_engine/
  models.py          Form IR, enums, results
  policy.py          FillPolicy
  detector.py        Confidence-based ATS detection
  scanner.py         Generic Form IR extraction
  locators.py        Locator ladders
  mapper.py          Semantic FieldMatch scoring
  actions.py         Verified Playwright helpers
  executor.py        Execute FillAction plans
  validator.py       Required-field / validation checks
  recovery.py        RecoveryRequired / ManualHandoff types
  learning.py        Learned mappings read/write/promote
  submission.py      Eligibility + confirmation
  diagnostics.py     Run IDs, traces, screenshots (local paths)
  state_machine.py   Adaptive multi-step loop helpers
  llm_fallback.py    Structured LLM classification
  orchestrator.py    Top-level run loop + CDP attach
  adapters/          greenhouse, lever, ashby, workday, generic
```

Config: `src/applypilot/config/application_engine.yaml` + `load_application_engine_config()`.

Docs: this file + `docs/form-engine.md` (user-facing).

Tests/fixtures under `tests/`.

---

## 6. Backward-compatibility strategy

1. **Default off:** `application_engine.enabled: false` — Claude apply path unchanged.
2. **Same Chrome owner:** form engine only connects over CDP.
3. **Same job queue:** still uses `acquire_job` / `mark_result` / existing status vocabulary.
4. **Additive schema:** new tables/columns only; no renames/drops.
5. **Opt-in CLI flags:** `--form-engine` / `--no-form-engine` override config without changing defaults.
6. **No silent skips:** unresolved required fields → `recovery_required` / failure, never pretend success.
7. **Auto-submit default false:** dry-run / ready_to_submit without clicking Submit unless configured.

---

## 7. Test strategy (fixtures + mocked Playwright)

| Layer | Approach |
|-------|----------|
| ATS detection | Static HTML fixtures + URL/title/DOM markers |
| Form IR scan | `page.set_content(fixture_html)` or parsed DOM helpers |
| Semantic matcher | Synthetic profile + fixture fields; score thresholds |
| Policy | Unit tests for sensitive / salary / narrative gates |
| Actions | Real Playwright against local HTML where useful; AsyncMock for edge cases |
| Adapters | Greenhouse/Lever fully fixture-tested; Ashby/Workday detection+scan fixtures |
| Learning | Test SQLite; promotion rules; no PII in signatures |
| LLM | Mocked JSON responses; reject malformed / unsafe |
| Submission | Success / unknown / validation-error / blocked fixtures |
| Compatibility | Patch test: disabled engine → still calls `run_job` |

**Never:** live ATS URLs, real resumes, real accounts, real CAPTCHA solving in CI/cloud.

---

## Design decisions (locked)

1. Dataclasses + Enums (no new Pydantic dependency).
2. Async Playwright API inside the package; `asyncio.run` from sync launcher.
3. Adapter selected at confidence ≥ `adapter_threshold` (0.85); else Generic.
4. Greenhouse + Lever: full fixture fill paths. Ashby + Workday: detection/state/scan + documented limits.
5. Login = session reuse + manual handoff; no CapSolver inside form engine.
6. Diagnostics under `~/.applypilot/form-engine-diagnostics/` (gitignored patterns).

---

## Out of scope / not claimed

- Live Greenhouse, Lever, Ashby, Workday, LinkedIn validation
- Real application submission
- CAPTCHA / OTP / MFA / anti-bot bypass
- Replacing Claude apply as the default path
