"""KeyboardTool — type text and press key combinations via the active
:class:`~opendesk.computer.Computer`."""

from __future__ import annotations

import asyncio
from typing import List, Literal, Optional

from pydantic import Field

from opendesk.computer.types import KeyAction, KeyEvent
from opendesk.tools.base import Tool, ToolContext, ToolResult


class KeyboardTool(Tool):
    """Type text or press keyboard keys and shortcuts."""

    name = "keyboard"
    description = (
        "Simulate keyboard input: type a string of text, press a single key, "
        "or send a key combination (hotkey). Unicode text is fully supported."
    )

    class Params(Tool.Params):
        action: Literal["type", "press", "hotkey", "hold"] = Field(
            description="Keyboard action: type, press, hotkey, or hold."
        )
        text: Optional[str] = Field(
            default=None,
            description="Text to type. Required for action='type'.",
        )
        key: Optional[str] = Field(
            default=None,
            description="Key name to press/hold: 'enter', 'escape', 'tab', 'f5', etc.",
        )
        keys: Optional[List[str]] = Field(
            default=None,
            description="Key names for hotkey, e.g. ['ctrl','c'].",
        )
        interval: float = Field(
            default=0.02,
            description="Seconds between keystrokes (action='type').",
        )
        settle_ms: int = Field(
            default=300,
            description="Milliseconds to wait after the action.",
        )
        hold_duration: float = Field(
            default=1.0,
            description="Seconds to hold key for action='hold'.",
        )

    async def execute(self, ctx: ToolContext, params: "KeyboardTool.Params") -> ToolResult:
        from opendesk.computer.sandbox import ActionType, get_sandbox

        if params.action == "type":
            arg_desc = f"type text: {(params.text or '')[:60]!r}"
        elif params.action == "press":
            arg_desc = f"press key: {params.key}"
        elif params.action == "hold":
            arg_desc = f"hold key: {params.key} for {params.hold_duration}s"
        else:
            arg_desc = f"hotkey: {'+'.join(params.keys or [])}"

        await ctx.check_permission(
            tool="keyboard", argument=arg_desc,
            description=f"Keyboard action -- {arg_desc}",
        )

        sandbox = get_sandbox(ctx.session_id)
        action_type_map = {
            "type": ActionType.KEYBOARD_TYPE,
            "press": ActionType.KEYBOARD_PRESS,
            "hotkey": ActionType.KEYBOARD_HOTKEY,
            "hold": ActionType.KEYBOARD_HOLD,
        }
        action_type = action_type_map[params.action]
        _replay_p = {
            "action": params.action,
            "text": params.text,
            "key": params.key,
            "keys": params.keys,
            "interval": params.interval,
            "hold_duration": params.hold_duration,
        }

        try:
            if params.action == "type":
                if not params.text:
                    return ToolResult(
                        title="Keyboard error",
                        output="text is required for action='type'.",
                        error=True,
                    )
                await ctx.computer.type_text(params.text, interval_ms=int(params.interval * 1000))
                preview = params.text[:40] + ("..." if len(params.text) > 40 else "")
                result_msg = f"Typed {len(params.text)} characters: {preview!r}"

            elif params.action == "press":
                if not params.key:
                    return ToolResult(
                        title="Keyboard error",
                        output="key is required for action='press'.",
                        error=True,
                    )
                await ctx.computer.press(params.key)
                result_msg = f"Pressed key: {params.key!r}"

            elif params.action == "hotkey":
                if not params.keys:
                    return ToolResult(
                        title="Keyboard error",
                        output="keys list is required for action='hotkey'.",
                        error=True,
                    )
                await ctx.computer.hotkey(params.keys)
                result_msg = f"Pressed hotkey: {'+'.join(params.keys)}"

            elif params.action == "hold":
                if not params.key:
                    return ToolResult(
                        title="Keyboard error",
                        output="key is required for action='hold'.",
                        error=True,
                    )
                await ctx.computer.key(KeyEvent(action=KeyAction.DOWN, keysym=params.key))
                await asyncio.sleep(max(0.0, params.hold_duration))
                await ctx.computer.key(KeyEvent(action=KeyAction.UP, keysym=params.key))
                result_msg = f"Held key {params.key!r} for {params.hold_duration:.2f}s then released."

            else:
                return ToolResult(
                    title="Keyboard error",
                    output=f"Unknown keyboard action: {params.action!r}",
                    error=True,
                )

            if params.settle_ms > 0:
                await asyncio.sleep(params.settle_ms / 1000.0)

        except Exception as exc:
            await sandbox.record_action(action_type, {"action": params.action}, error=str(exc),
                                        replay_params=_replay_p)
            return ToolResult(
                title="Keyboard error",
                output=f"Keyboard action failed: {exc}",
                error=True,
            )

        await sandbox.record_action(
            action_type, {"action": params.action}, result=result_msg,
            replay_params=_replay_p,
        )
        return ToolResult(title=f"Keyboard: {params.action}", output=result_msg)
