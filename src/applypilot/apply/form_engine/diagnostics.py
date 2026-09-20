"""Cloud-safe diagnostics: local paths only, optional Playwright tracing."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from applypilot import config


def new_run_id() -> str:
    return f"fe_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"


def diagnostics_dir_for(run_id: str, base: Path | None = None) -> Path:
    root = base or config.FORM_ENGINE_DIAG_DIR
    path = root / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


class Diagnostics:
    """Records state transitions and field events; optional screenshots/traces."""

    def __init__(
        self,
        run_id: str | None = None,
        *,
        enabled: bool = False,
        trace_on_failure: bool = True,
        screenshot_on_failure: bool = True,
        base_dir: Path | None = None,
    ):
        self.run_id = run_id or new_run_id()
        self.enabled = enabled
        self.trace_on_failure = trace_on_failure
        self.screenshot_on_failure = screenshot_on_failure
        self.dir = diagnostics_dir_for(self.run_id, base_dir)
        self.events: list[dict[str, Any]] = []
        self.transitions: list[dict[str, Any]] = []
        self.trace_path: Path | None = None
        self.screenshot_path: Path | None = None
        self._tracing = False

    def record_transition(self, from_state: str, to_state: str, **meta: Any) -> None:
        entry = {
            "from": from_state,
            "to": to_state,
            "at": datetime.now(timezone.utc).isoformat(),
            **meta,
        }
        self.transitions.append(entry)
        if self.enabled:
            self._append_jsonl("transitions.jsonl", entry)

    def record_field_event(self, **event: Any) -> None:
        # Strip any accidental raw values
        event.pop("raw_value", None)
        event.pop("value", None)
        event["at"] = datetime.now(timezone.utc).isoformat()
        self.events.append(event)
        if self.enabled:
            self._append_jsonl("field_events.jsonl", event)

    def _append_jsonl(self, name: str, obj: dict) -> None:
        path = self.dir / name
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, default=str) + "\n")

    def write_summary(self, summary: dict[str, Any]) -> Path:
        path = self.dir / "summary.json"
        path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        return path

    async def start_trace(self, context: Any) -> None:
        if not self.enabled:
            return
        try:
            await context.tracing.start(screenshots=True, snapshots=True, sources=False)
            self._tracing = True
        except Exception:
            self._tracing = False

    async def stop_trace(self, context: Any, *, save: bool = False) -> Path | None:
        if not self._tracing:
            return None
        out = self.dir / "trace.zip"
        try:
            if save or self.trace_on_failure:
                await context.tracing.stop(path=str(out))
                self.trace_path = out
            else:
                await context.tracing.stop()
            self._tracing = False
            return self.trace_path
        except Exception:
            self._tracing = False
            return None

    async def screenshot(self, page: Any, name: str = "failure.png") -> Path | None:
        if not (self.enabled or self.screenshot_on_failure):
            return None
        path = self.dir / name
        try:
            await page.screenshot(path=str(path), full_page=True)
            self.screenshot_path = path
            return path
        except Exception:
            return None


def dataclass_to_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    return obj
