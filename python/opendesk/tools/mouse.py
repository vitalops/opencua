"""MouseTool — control the mouse pointer via the active
:class:`~opendesk.computer.Computer`."""

from __future__ import annotations

import asyncio
from typing import Literal, Optional

from pydantic import Field

from opendesk.computer.types import Point, PointerButton
from opendesk.tools.base import Tool, ToolContext, ToolResult


_ACTION_TO_BUTTON: dict[str, PointerButton] = {
    "click": PointerButton.LEFT,
    "double_click": PointerButton.LEFT,
    "triple_click": PointerButton.LEFT,
    "right_click": PointerButton.RIGHT,
    "middle_click": PointerButton.MIDDLE,
    "left_down": PointerButton.LEFT,
    "left_up": PointerButton.LEFT,
}
_CLICK_COUNT: dict[str, int] = {
    "click": 1, "double_click": 2, "triple_click": 3,
    "right_click": 1, "middle_click": 1,
}


class MouseTool(Tool):
    """Move, click, scroll, or drag the mouse pointer."""

    name = "mouse"
    description = (
        "Control the mouse: move to a position, click (left/right/middle), "
        "double-click, triple-click, scroll, or drag from one point to another. "
        "Always provide image_width and image_height from the screenshot tool "
        "output for correct Retina/HiDPI coordinate translation."
    )

    class Params(Tool.Params):
        action: Literal[
            "move", "click", "double_click", "triple_click",
            "right_click", "middle_click",
            "left_down", "left_up",
            "scroll", "drag",
            "cursor_position",
        ] = Field(
            description=(
                "Mouse action: move, click, double_click, triple_click, "
                "right_click, middle_click, left_down, left_up, scroll, drag, cursor_position"
            )
        )
        x: int = Field(default=0, description="X coordinate as it appears in the screenshot.")
        y: int = Field(default=0, description="Y coordinate as it appears in the screenshot.")
        end_x: Optional[int] = Field(default=None, description="Target X for drag action.")
        end_y: Optional[int] = Field(default=None, description="Target Y for drag action.")
        image_width: Optional[int] = Field(
            default=None,
            description=(
                "Width of the screenshot the coordinates were read from. "
                "ALWAYS provide this for correct Retina/HiDPI scaling."
            ),
        )
        image_height: Optional[int] = Field(
            default=None,
            description="Height of the screenshot. Provide alongside image_width.",
        )
        direction: Optional[Literal["up", "down", "left", "right"]] = Field(
            default=None,
            description="Scroll direction. Used with action='scroll'.",
        )
        amount: int = Field(default=3, description="Scroll amount in clicks.")
        duration: float = Field(default=0.25, description="Movement duration in seconds.")
        settle_ms: int = Field(
            default=500,
            description="Milliseconds to wait after the action for the UI to settle.",
        )

    async def execute(self, ctx: ToolContext, params: "MouseTool.Params") -> ToolResult:
        from opendesk.computer.sandbox import ActionType, get_sandbox

        sandbox = get_sandbox(ctx.session_id)

        if params.action == "cursor_position":
            await ctx.check_permission(
                tool="mouse", argument="cursor_position",
                description="Query current cursor position",
            )
            try:
                pos = await ctx.computer.cursor_position()
                cx, cy = int(pos.x), int(pos.y)
                await sandbox.record_action(
                    ActionType.CURSOR_POSITION, {}, result=f"({cx},{cy})",
                )
                return ToolResult(title="Cursor position", output=f"Current cursor: ({cx}, {cy})")
            except Exception as exc:
                return ToolResult(title="Cursor position error", output=str(exc), error=True)

        action_label = f"mouse {params.action} at ({params.x}, {params.y})"
        await ctx.check_permission(
            tool="mouse", argument=action_label,
            description=f"Mouse action: {action_label}",
        )

        logical_point, logical_end, scale_note = await self._to_logical(ctx, params)

        if not sandbox.is_coordinate_allowed(int(logical_point.x), int(logical_point.y)):
            return ToolResult(
                title="Mouse action denied",
                output=f"Coordinate ({int(logical_point.x)}, {int(logical_point.y)}) is outside the permitted region.",
                error=True,
            )

        action_type_map = {
            "move": ActionType.MOUSE_MOVE,
            "click": ActionType.MOUSE_CLICK,
            "double_click": ActionType.MOUSE_CLICK,
            "triple_click": ActionType.MOUSE_CLICK,
            "right_click": ActionType.MOUSE_CLICK,
            "middle_click": ActionType.MOUSE_CLICK,
            "left_down": ActionType.MOUSE_DOWN,
            "left_up": ActionType.MOUSE_UP,
            "scroll": ActionType.MOUSE_SCROLL,
            "drag": ActionType.MOUSE_DRAG,
        }
        action_type = action_type_map.get(params.action, ActionType.MOUSE_CLICK)
        record_params = {
            "action": params.action, "x": params.x, "y": params.y,
            "amount": params.amount, "duration": params.duration,
        }
        if params.action == "drag":
            record_params["end_x"] = params.end_x
            record_params["end_y"] = params.end_y

        replay_params = {
            "action": params.action, "x": params.x, "y": params.y,
            "direction": params.direction, "amount": params.amount,
            "duration": params.duration, "settle_ms": params.settle_ms,
            "image_width": params.image_width, "image_height": params.image_height,
        }
        if params.action == "drag":
            replay_params["end_x"] = params.end_x
            replay_params["end_y"] = params.end_y

        try:
            result_msg = await self._dispatch(ctx, params, logical_point, logical_end)
            if params.settle_ms > 0:
                await asyncio.sleep(params.settle_ms / 1000.0)
        except Exception as exc:
            await sandbox.record_action(action_type, record_params, error=str(exc),
                                        replay_params=replay_params)
            return ToolResult(title="Mouse error", output=f"Mouse action failed: {exc}", error=True)

        await sandbox.record_action(action_type, record_params, result=result_msg,
                                    replay_params=replay_params)
        return ToolResult(title=f"Mouse: {params.action}", output=result_msg + scale_note)

    async def _to_logical(
        self, ctx: ToolContext, params: "MouseTool.Params"
    ) -> tuple[Point, Optional[Point], str]:
        """Translate the image-space coordinates the agent passes to logical pixels."""
        if not (params.image_width and params.image_height):
            return (
                Point(x=params.x, y=params.y),
                Point(x=params.end_x, y=params.end_y) if params.end_x is not None and params.end_y is not None else None,
                "",
            )
        displays = await ctx.computer.displays()
        if not displays:
            return (
                Point(x=params.x, y=params.y),
                Point(x=params.end_x, y=params.end_y) if params.end_x is not None and params.end_y is not None else None,
                "",
            )
        screen_w = displays[0].bounds.width
        screen_h = displays[0].bounds.height
        sx = screen_w / params.image_width
        sy = screen_h / params.image_height
        if abs(sx - 1.0) <= 0.02 and abs(sy - 1.0) <= 0.02:
            scaled = Point(x=params.x, y=params.y)
            scaled_end = (
                Point(x=params.end_x, y=params.end_y)
                if params.end_x is not None and params.end_y is not None else None
            )
            return scaled, scaled_end, ""
        scaled = Point(x=round(params.x * sx), y=round(params.y * sy))
        scaled_end = None
        if params.end_x is not None and params.end_y is not None:
            scaled_end = Point(x=round(params.end_x * sx), y=round(params.end_y * sy))
        note = (
            f" [image ({params.x},{params.y}) -> logical "
            f"({int(scaled.x)},{int(scaled.y)}) scale {sx:.3f}x{sy:.3f}]"
        )
        return scaled, scaled_end, note

    async def _dispatch(
        self,
        ctx: ToolContext,
        params: "MouseTool.Params",
        point: Point,
        end: Optional[Point],
    ) -> str:
        comp = ctx.computer

        if params.action == "move":
            from opendesk.computer.types import PointerEvent, PointerAction
            await comp.pointer(PointerEvent(action=PointerAction.MOVE, point=point))
            return f"Moved mouse to ({int(point.x)}, {int(point.y)})."

        if params.action in ("click", "double_click", "triple_click", "right_click", "middle_click"):
            await comp.click(
                point,
                button=_ACTION_TO_BUTTON[params.action],
                count=_CLICK_COUNT[params.action],
            )
            return f"{params.action.replace('_', '-').capitalize()} at ({int(point.x)}, {int(point.y)})."

        if params.action == "left_down":
            from opendesk.computer.types import PointerEvent, PointerAction
            await comp.pointer(PointerEvent(action=PointerAction.MOVE, point=point))
            await comp.pointer(PointerEvent(
                action=PointerAction.DOWN, point=point, button=PointerButton.LEFT,
            ))
            return f"Left button pressed at ({int(point.x)}, {int(point.y)}) -- button is held."

        if params.action == "left_up":
            from opendesk.computer.types import PointerEvent, PointerAction
            await comp.pointer(PointerEvent(action=PointerAction.MOVE, point=point))
            await comp.pointer(PointerEvent(
                action=PointerAction.UP, point=point, button=PointerButton.LEFT,
            ))
            return f"Left button released at ({int(point.x)}, {int(point.y)})."

        if params.action == "scroll":
            direction = params.direction
            amount = params.amount
            dx, dy = 0.0, 0.0
            if direction == "up":
                dy = amount
            elif direction == "down":
                dy = -amount
            elif direction == "right":
                dx = amount
            elif direction == "left":
                dx = -amount
            else:
                dy = amount
            await comp.scroll(point, dx=dx, dy=dy)
            label = direction or ("up" if amount > 0 else "down")
            return f"Scrolled {label} {abs(amount)} click(s) at ({int(point.x)}, {int(point.y)})."

        if params.action == "drag":
            if end is None:
                raise ValueError("end_x and end_y are required for drag action.")
            await comp.drag(point, end)
            return f"Dragged from ({int(point.x)}, {int(point.y)}) to ({int(end.x)}, {int(end.y)})."

        raise ValueError(f"Unknown mouse action: {params.action!r}")
