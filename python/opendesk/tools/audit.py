"""Audit tool — expose the session audit log inside any MCP or agent session."""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import Field

from opendesk.tools.base import Tool, ToolContext, ToolResult


# ActionTypes that produce no side-effects and should be skipped during replay.
_SKIP_REPLAY = frozenset({
    "screenshot", "cursor_position", "app_list", "clipboard_read", "ocr",
})

# Maps audit action value → tool name for lazy import during replay.
_ACTION_TO_TOOL: dict[str, str] = {
    "mouse_click": "mouse", "mouse_move": "mouse", "mouse_scroll": "mouse",
    "mouse_drag": "mouse", "mouse_down": "mouse", "mouse_up": "mouse",
    "keyboard_type": "keyboard", "keyboard_press": "keyboard",
    "keyboard_hotkey": "keyboard", "keyboard_hold": "keyboard",
    "app_open": "app", "app_close": "app", "app_focus": "app",
    "clipboard_write": "clipboard",
    "ui_action": "ui",
}

_TOOL_CLASSES = {
    "mouse": "opendesk.tools.mouse:MouseTool",
    "keyboard": "opendesk.tools.keyboard:KeyboardTool",
    "app": "opendesk.tools.app:AppTool",
    "clipboard": "opendesk.tools.clipboard:ClipboardTool",
    "ui": "opendesk.tools.ui:UITool",
}


def _load_tool(name: str):
    import importlib
    module_path, cls_name = _TOOL_CLASSES[name].split(":")
    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)()


class AuditTool(Tool):
    """Show or replay the session audit log.

    action='show'   — display the log (summary or full timestamped list).
    action='replay' — re-execute every replayable action recorded this session.
    """

    name = "audit"
    description = (
        "Show or replay the session audit log.\n"
        "  action='show'   — display recorded actions (format='summary' or 'full')\n"
        "  action='replay' — re-execute every action from this session "
        "(skips screenshots, reads, and optionally failed actions)"
    )

    class Params(Tool.Params):
        action: Literal["show", "replay"] = Field(
            default="show",
            description="'show' displays the log; 'replay' re-executes actions.",
        )
        format: Literal["summary", "full"] = Field(
            default="full",
            description="For action='show': 'summary' = one-line count, 'full' = timestamped log.",
        )
        session_id: Optional[str] = Field(
            default=None,
            description="Session to inspect/replay. Defaults to the current session.",
        )
        skip_errors: bool = Field(
            default=True,
            description="For action='replay': skip actions that originally errored.",
        )

    async def execute(self, ctx: ToolContext, params: "AuditTool.Params") -> ToolResult:
        from opendesk.computer.sandbox import get_sandbox

        session_id = params.session_id or ctx.session_id
        sandbox = get_sandbox(session_id)

        if params.action == "replay":
            return await self._replay(ctx, sandbox, params.skip_errors)

        # --- show ---
        if params.format == "summary":
            return ToolResult(title="Audit summary", output=sandbox.summary())

        log = sandbox.export_audit_log()
        if not log:
            return ToolResult(
                title="Audit log",
                output=f"No actions recorded yet for session '{session_id}'.",
            )

        import datetime
        lines: list[str] = [f"Audit log — session '{session_id}' ({len(log)} actions)\n"]
        for i, entry in enumerate(log, 1):
            ts = datetime.datetime.fromtimestamp(entry["timestamp"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            error_tag = f"  ERROR: {entry['error']}" if entry["error"] else ""
            replayable = "✓" if entry.get("replay_params") else " "
            params_str = json.dumps(entry["params"], ensure_ascii=False)
            lines.append(
                f"[{i:>3}] [{replayable}] [{ts}] {entry['action']:<20} {params_str}{error_tag}"
            )

        lines.append("\n✓ = replayable via audit(action='replay')")
        return ToolResult(title="Audit log", output="\n".join(lines))

    async def _replay(
        self, ctx: ToolContext, sandbox, skip_errors: bool
    ) -> ToolResult:
        log = sandbox.export_audit_log()
        if not log:
            return ToolResult(
                title="Audit replay",
                output="No actions to replay — the audit log is empty.",
            )

        replayable = [
            e for e in log
            if e["action"] not in _SKIP_REPLAY
            and e.get("replay_params") is not None
            and (not skip_errors or not e["error"])
        ]

        if not replayable:
            return ToolResult(
                title="Audit replay",
                output=(
                    "No replayable actions found. "
                    "Replayable actions require replay_params — only actions recorded "
                    "in this session (not imported logs) support direct replay."
                ),
            )

        results: list[str] = [f"Replaying {len(replayable)} action(s)...\n"]
        ok = 0
        failed = 0

        for entry in replayable:
            action_val = entry["action"]
            tool_name = _ACTION_TO_TOOL.get(action_val)
            if tool_name is None:
                results.append(f"  SKIP  {action_val} (no tool mapping)")
                continue

            tool = _load_tool(tool_name)
            rp = entry["replay_params"]
            try:
                result = await tool.execute(ctx, tool.parse_params(rp))
                status = "ERROR" if result.error else "OK"
                results.append(f"  {status:<5} {action_val:<20} {result.output[:80]}")
                if result.error:
                    failed += 1
                else:
                    ok += 1
            except Exception as exc:
                results.append(f"  ERROR {action_val:<20} {exc}")
                failed += 1

        results.append(f"\nDone — {ok} succeeded, {failed} failed.")
        return ToolResult(title="Audit replay", output="\n".join(results))
