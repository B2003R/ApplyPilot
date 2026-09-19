"""CapSolver client for apply-stage CAPTCHA solving.

Solves CAPTCHAs via the CapSolver REST API in Python so Claude Code does not
need to run the multi-step createTask/poll/inject ritual itself.

Usage (from Claude Bash or CLI):
    python -m applypilot.apply.captcha solve \\
        --type hcaptcha --url URL --sitekey KEY

Prints JSON: {"ok": true, "token": "...", "inject_js": "() => {...}"}
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import httpx

from applypilot import config

log = logging.getLogger(__name__)

API_BASE = "https://api.capsolver.com"

# CapSolver task type mapping from detect labels
TASK_TYPES: dict[str, str] = {
    "hcaptcha": "HCaptchaTaskProxyLess",
    "recaptchav2": "ReCaptchaV2TaskProxyLess",
    "recaptchav3": "ReCaptchaV3TaskProxyLess",
    "turnstile": "AntiTurnstileTaskProxyLess",
    "funcaptcha": "FunCaptchaTaskProxyLess",
}

# JS that Claude runs via browser_evaluate to detect CAPTCHA type + sitekey
DETECT_JS = """() => {
  const r = {};
  const url = window.location.href;
  const hc = document.querySelector('.h-captcha, [data-hcaptcha-sitekey]');
  if (hc) {
    r.type = 'hcaptcha'; r.sitekey = hc.dataset.sitekey || hc.dataset.hcaptchaSitekey;
  }
  if (!r.type && document.querySelector('script[src*="hcaptcha.com"], iframe[src*="hcaptcha.com"]')) {
    const el = document.querySelector('[data-sitekey]');
    if (el) { r.type = 'hcaptcha'; r.sitekey = el.dataset.sitekey; }
  }
  if (!r.type) {
    const cf = document.querySelector('.cf-turnstile, [data-turnstile-sitekey]');
    if (cf) {
      r.type = 'turnstile'; r.sitekey = cf.dataset.sitekey || cf.dataset.turnstileSitekey;
      if (cf.dataset.action) r.action = cf.dataset.action;
      if (cf.dataset.cdata) r.cdata = cf.dataset.cdata;
    }
  }
  if (!r.type && document.querySelector('script[src*="challenges.cloudflare.com"]')) {
    r.type = 'turnstile_script_only'; r.note = 'Wait 3s and re-detect.';
  }
  if (!r.type) {
    const s = document.querySelector('script[src*="recaptcha"][src*="render="]');
    if (s) {
      const m = s.src.match(/render=([^&]+)/);
      if (m && m[1] !== 'explicit') { r.type = 'recaptchav3'; r.sitekey = m[1]; }
    }
  }
  if (!r.type) {
    const rc = document.querySelector('.g-recaptcha');
    if (rc) { r.type = 'recaptchav2'; r.sitekey = rc.dataset.sitekey; }
  }
  if (!r.type && document.querySelector('script[src*="recaptcha"]')) {
    const el = document.querySelector('[data-sitekey]');
    if (el) { r.type = 'recaptchav2'; r.sitekey = el.dataset.sitekey; }
  }
  if (!r.type) {
    const fc = document.querySelector('#FunCaptcha, [data-pkey], .funcaptcha');
    if (fc) { r.type = 'funcaptcha'; r.sitekey = fc.dataset.pkey; }
  }
  if (!r.type && document.querySelector('script[src*="arkoselabs"], script[src*="funcaptcha"]')) {
    const el = document.querySelector('[data-pkey]');
    if (el) { r.type = 'funcaptcha'; r.sitekey = el.dataset.pkey; }
  }
  if (r.type) { r.url = url; return r; }
  return null;
}"""


def _api_key() -> str:
    config.load_env()
    return os.environ.get("CAPSOLVER_API_KEY", "").strip()


def is_configured() -> bool:
    """Return True when CAPSOLVER_API_KEY is set."""
    return bool(_api_key())


def build_inject_js(captcha_type: str, token: str) -> str:
    """Return a browser_evaluate function body that injects the CAPTCHA token."""
    # Escape for embedding inside a JS string literal
    tok = token.replace("\\", "\\\\").replace("'", "\\'")
    ctype = captcha_type.lower().replace(" ", "").replace("_", "")

    if ctype in ("recaptchav2", "recaptchav3", "recaptcha"):
        return f"""() => {{
  const token = '{tok}';
  document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {{
    el.value = token; el.style.display = 'block';
  }});
  if (window.___grecaptcha_cfg) {{
    const clients = window.___grecaptcha_cfg.clients;
    for (const key in clients) {{
      const walk = (obj, d) => {{
        if (d > 4 || !obj) return;
        for (const k in obj) {{
          if (typeof obj[k] === 'function' && k.length < 3) try {{ obj[k](token); }} catch(e) {{}}
          else if (typeof obj[k] === 'object') walk(obj[k], d+1);
        }}
      }};
      walk(clients[key], 0);
    }}
  }}
  return 'injected';
}}"""

    if ctype == "hcaptcha":
        return f"""() => {{
  const token = '{tok}';
  const ta = document.querySelector('[name="h-captcha-response"], textarea[name*="hcaptcha"]');
  if (ta) ta.value = token;
  document.querySelectorAll('iframe[data-hcaptcha-response]').forEach(
    f => f.setAttribute('data-hcaptcha-response', token)
  );
  return 'injected';
}}"""

    if ctype == "turnstile":
        return f"""() => {{
  const token = '{tok}';
  const inp = document.querySelector('[name="cf-turnstile-response"], input[name*="turnstile"]');
  if (inp) inp.value = token;
  return 'injected';
}}"""

    if ctype == "funcaptcha":
        return f"""() => {{
  const token = '{tok}';
  const inp = document.querySelector('#FunCaptcha-Token, input[name="fc-token"]');
  if (inp) inp.value = token;
  return 'injected';
}}"""

    # Generic fallback: try common hidden response fields
    return f"""() => {{
  const token = '{tok}';
  document.querySelectorAll(
    '[name="g-recaptcha-response"], [name="h-captcha-response"], [name="cf-turnstile-response"]'
  ).forEach(el => {{ el.value = token; }});
  return 'injected';
}}"""


def solve(
    captcha_type: str,
    website_url: str,
    website_key: str,
    *,
    page_action: str | None = None,
    metadata: dict | None = None,
    poll_interval: float = 3.0,
    max_polls: int = 10,
) -> dict:
    """Solve a CAPTCHA via CapSolver and return token + inject JS.

    Returns:
        {"ok": True, "token": str, "inject_js": str} on success
        {"ok": False, "error": str} on failure
    """
    key = _api_key()
    if not key:
        return {"ok": False, "error": "CAPSOLVER_API_KEY not configured"}

    ctype = captcha_type.lower().strip()
    if ctype == "turnstile_script_only":
        return {"ok": False, "error": "turnstile_script_only — wait and re-detect"}

    task_type = TASK_TYPES.get(ctype)
    if not task_type:
        return {"ok": False, "error": f"Unknown captcha type: {captcha_type}"}

    task: dict = {
        "type": task_type,
        "websiteURL": website_url,
        "websiteKey": website_key,
    }
    if ctype == "recaptchav3":
        task["pageAction"] = page_action or "submit"
    if ctype == "turnstile" and metadata:
        task["metadata"] = metadata

    try:
        with httpx.Client(timeout=60) as client:
            create = client.post(
                f"{API_BASE}/createTask",
                json={"clientKey": key, "task": task},
            )
            create.raise_for_status()
            create_data = create.json()

            if create_data.get("errorId", 0) > 0:
                return {
                    "ok": False,
                    "error": create_data.get("errorDescription")
                    or create_data.get("errorCode")
                    or "createTask failed",
                }

            task_id = create_data.get("taskId")
            if not task_id:
                return {"ok": False, "error": "createTask returned no taskId"}

            for _ in range(max_polls):
                time.sleep(poll_interval)
                poll = client.post(
                    f"{API_BASE}/getTaskResult",
                    json={"clientKey": key, "taskId": task_id},
                )
                poll.raise_for_status()
                poll_data = poll.json()

                if poll_data.get("errorId", 0) > 0:
                    return {
                        "ok": False,
                        "error": poll_data.get("errorDescription")
                        or poll_data.get("errorCode")
                        or "getTaskResult failed",
                    }

                status = poll_data.get("status")
                if status == "ready":
                    solution = poll_data.get("solution") or {}
                    token = (
                        solution.get("gRecaptchaResponse")
                        or solution.get("token")
                        or solution.get("text")
                        or ""
                    )
                    if not token:
                        return {"ok": False, "error": "ready but no token in solution"}
                    return {
                        "ok": True,
                        "token": token,
                        "inject_js": build_inject_js(ctype, token),
                    }
                # status == "processing" -> keep polling

            return {"ok": False, "error": f"timed out after {max_polls} polls"}

    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"HTTP error: {exc}"}
    except Exception as exc:
        log.exception("CapSolver solve failed")
        return {"ok": False, "error": str(exc)}


def _cli_solve(args: argparse.Namespace) -> int:
    metadata = None
    if args.action or args.cdata:
        metadata = {}
        if args.action:
            metadata["action"] = args.action
        if args.cdata:
            metadata["cdata"] = args.cdata

    result = solve(
        args.type,
        args.url,
        args.sitekey,
        page_action=args.action,
        metadata=metadata,
    )
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


def _cli_detect_js(_args: argparse.Namespace) -> int:
    print(DETECT_JS)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ApplyPilot CapSolver helper")
    sub = parser.add_subparsers(dest="command", required=True)

    solve_p = sub.add_parser("solve", help="Solve a CAPTCHA and print token JSON")
    solve_p.add_argument("--type", required=True, help="hcaptcha|recaptchav2|recaptchav3|turnstile|funcaptcha")
    solve_p.add_argument("--url", required=True, help="Page URL")
    solve_p.add_argument("--sitekey", required=True, help="CAPTCHA site key")
    solve_p.add_argument("--action", default=None, help="pageAction / turnstile action")
    solve_p.add_argument("--cdata", default=None, help="Turnstile cdata")
    solve_p.set_defaults(func=_cli_solve)

    detect_p = sub.add_parser("detect-js", help="Print CAPTCHA detect JS for browser_evaluate")
    detect_p.set_defaults(func=_cli_detect_js)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
