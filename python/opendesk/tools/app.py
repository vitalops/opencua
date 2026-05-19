"""AppTool — open, close, focus, and list desktop applications via the
active :class:`~opendesk.computer.Computer`."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from opendesk.tools.base import Tool, ToolContext, ToolResult


class AppTool(Tool):
    """Open, close, focus, or list desktop applications."""

    name = "app"
    description = (
        "Interact with desktop applications: open an app by name, close it, "
        "bring it to the foreground, or list all currently running windows."
    )

    class Params(Tool.Params):
        action: Literal["open", "close", "focus", "list"] = Field(
            description="App action: open, close, focus, or list."
        )
        name: Optional[str] = Field(
            default=None,
            description=(
                "Application name (e.g. 'Terminal', 'Google Chrome', 'VS Code') "
                "or full executable path. Required for open/close/focus."
            ),
        )

    async def execute(self, ctx: ToolContext, params: "AppTool.Params") -> ToolResult:
        from opendesk.computer.sandbox import ActionType, get_sandbox

        app_arg = params.name or "(list)"
        await ctx.check_permission(
            tool="app", argument=f"{params.action} {app_arg}",
            description=f"App control: {params.action} '{app_arg}'",
        )

        sandbox = get_sandbox(ctx.session_id)
        if params.action in ("open", "close", "focus") and params.name:
            if not sandbox.is_app_allowed(params.name):
                return ToolResult(
                    title="App action denied",
                    output=(
                        f"Application '{params.name}' is not in the allow-list. "
                        f"Allowed: {sandbox.allowed_apps or ['(all)']}"
                    ),
                    error=True,
                )

        action_type_map = {
            "open": ActionType.APP_OPEN, "close": ActionType.APP_CLOSE,
            "focus": ActionType.APP_FOCUS, "list": ActionType.APP_LIST,
        }
        action_type = action_type_map[params.action]

        _replay_p = {"action": params.action, "name": params.name}

        try:
            if params.action == "list":
                names = await ctx.computer.list_apps()
                result_msg = (
                    f"Running applications ({len(names)}):\n"
                    + "\n".join(f"  * {n}" for n in names)
                ) if names else "No running applications detected."
            elif params.name is None:
                raise ValueError(f"name is required for action='{params.action}'.")
            elif params.action == "open":
                await ctx.computer.open_app(params.name)
                result_msg = f"Opened '{params.name}'."
            elif params.action == "close":
                await ctx.computer.close_app(params.name)
                result_msg = f"Closed '{params.name}'."
            elif params.action == "focus":
                await ctx.computer.focus_app(params.name)
                result_msg = f"Focused '{params.name}'."
            else:
                raise ValueError(f"Unknown app action: {params.action!r}")
        except Exception as exc:
            await sandbox.record_action(
                action_type, {"action": params.action, "name": params.name},
                error=str(exc), replay_params=_replay_p,
            )
            return ToolResult(
                title="App error",
                output=f"App action '{params.action}' failed: {exc}",
                error=True,
            )

        await sandbox.record_action(
            action_type, {"action": params.action, "name": params.name},
            result=result_msg[:200], replay_params=_replay_p,
        )
        return ToolResult(
            title=f"App: {params.action} '{params.name or ''}'",
            output=result_msg,
        )
