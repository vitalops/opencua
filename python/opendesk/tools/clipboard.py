"""ClipboardTool — read and write the system clipboard via the active
:class:`~opendesk.computer.Computer`."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from opendesk.tools.base import Tool, ToolContext, ToolResult


class ClipboardTool(Tool):
    """Read or write the system clipboard."""

    name = "clipboard"
    description = (
        "Read the current clipboard text or write new text to the clipboard. "
        "Use 'read' to retrieve copied text; 'write' to place text on the clipboard."
    )

    class Params(Tool.Params):
        action: Literal["read", "write"] = Field(
            description="Clipboard action: read or write."
        )
        text: Optional[str] = Field(
            default=None,
            description="Text to place on the clipboard. Required for action='write'.",
        )

    async def execute(self, ctx: ToolContext, params: "ClipboardTool.Params") -> ToolResult:
        from opendesk.computer.sandbox import ActionType, get_sandbox

        arg_desc = (
            f"clipboard write: {(params.text or '')[:40]!r}"
            if params.action == "write"
            else "clipboard read"
        )
        await ctx.check_permission(
            tool="clipboard", argument=arg_desc,
            description=f"Clipboard: {arg_desc}",
        )

        sandbox = get_sandbox(ctx.session_id)
        action_type = ActionType.CLIPBOARD_READ if params.action == "read" else ActionType.CLIPBOARD_WRITE

        _replay_p = {"action": params.action, "text": params.text}

        try:
            if params.action == "read":
                text = await ctx.computer.clipboard_text() or ""
                result_msg = text if text else "(clipboard is empty)"
            else:
                if not params.text:
                    return ToolResult(
                        title="Clipboard error",
                        output="'text' is required for action='write'.",
                        error=True,
                    )
                await ctx.computer.clipboard_set_text(params.text)
                preview = params.text[:80] + ("..." if len(params.text) > 80 else "")
                result_msg = f"Clipboard set ({len(params.text)} chars): {preview!r}"
        except Exception as exc:
            await sandbox.record_action(action_type, {"action": params.action}, error=str(exc),
                                        replay_params=_replay_p)
            return ToolResult(title="Clipboard error", output=str(exc), error=True)

        await sandbox.record_action(
            action_type,
            {"action": params.action, "text_len": len(params.text or "")},
            result=result_msg[:200],
            replay_params=_replay_p,
        )
        return ToolResult(title=f"Clipboard: {params.action}", output=result_msg)
