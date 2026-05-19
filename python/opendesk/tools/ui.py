"""UITool — accessibility-tree based UI interaction via the active
:class:`~opendesk.computer.Computer`.

Prefer this tool over raw mouse clicks: it resolves elements by their visible
name, so it works at any window position / screen resolution.  Internally it
queries :meth:`Computer.ui_tree` to find a target and then clicks at the
element's logical bounds.center.

The UI tool itself is platform-agnostic — all platform-specific accessibility
behaviour lives in the active :class:`Computer` backend.
"""

from __future__ import annotations

import asyncio
from typing import List, Literal, Optional

from pydantic import Field

from opendesk.computer.base import CapabilityUnsupported
from opendesk.computer.types import Capability, Modifier, Point, UIElement
from opendesk.tools.base import Tool, ToolContext, ToolResult


_MODIFIER_NAMES: dict[str, Modifier] = {
    "command": Modifier.META, "cmd": Modifier.META, "meta": Modifier.META,
    "win": Modifier.META, "super": Modifier.META,
    "ctrl": Modifier.CTRL, "control": Modifier.CTRL,
    "shift": Modifier.SHIFT,
    "alt": Modifier.ALT, "option": Modifier.ALT,
    "fn": Modifier.FN,
}


def _to_modifiers(names: list[str]) -> list[Modifier]:
    out: list[Modifier] = []
    for n in names:
        mod = _MODIFIER_NAMES.get(n.lower())
        if mod is not None and mod not in out:
            out.append(mod)
    return out


def _flatten_tree(root: UIElement) -> list[UIElement]:
    """Depth-first flatten of a UIElement tree."""
    out: list[UIElement] = [root]
    for child in root.children:
        out.extend(_flatten_tree(child))
    return out


def _find_element(
    root: UIElement, *, title: Optional[str], role: Optional[str],
) -> Optional[UIElement]:
    title_l = title.lower() if title else None
    role_l = role.lower() if role else None
    for node in _flatten_tree(root):
        name_match = not title_l or (node.name and title_l in node.name.lower())
        role_match = not role_l or role_l in node.role.lower()
        if name_match and role_match and (title_l or role_l):
            if node.bounds is not None or node.actions:
                return node
    return None


async def _try_perform_action(
    comp, element: UIElement, action: str, app: Optional[str],
) -> bool:
    """Try the native a11y action path; return True on success, False otherwise.

    Used by the UI tool to prefer high-fidelity native a11y invocation
    (AppleScript ``click elem``, pyatspi ``doAction``, pywinauto
    ``click_input``) and degrade gracefully to a pointer click at the
    element's bounds.center when the backend doesn't support it.
    """
    try:
        manifest = comp.capabilities()
    except Exception:
        return False
    if not manifest.has(Capability.UI_ACTIONS):
        return False
    try:
        await comp.perform_ui_action(element, action, app=app)
        return True
    except CapabilityUnsupported:
        return False
    except (RuntimeError, ValueError):
        return False


def _format_tree(root: UIElement, max_lines: int = 200) -> str:
    lines: list[str] = []
    if root.name or root.role:
        lines.append(f"Window: {root.name or root.role}")

    def visit(node: UIElement, depth: int) -> None:
        if len(lines) >= max_lines:
            return
        if node is not root and (node.name or node.role):
            indent = "  " * depth
            label = node.name or "(unnamed)"
            lines.append(f"{indent}[{node.role}] {label}")
        for child in node.children:
            visit(child, depth + 1)

    visit(root, 0)
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


class UITool(Tool):
    """Interact with desktop UI elements by name — no pixel coordinates needed.

    ALWAYS try this before the mouse tool.  Use mouse clicks only for
    unlabelled canvas areas (games, video players, drawing tools) where no
    accessible elements exist.
    """

    name = "ui"
    description = (
        "Interact with UI elements by name on any OS — no coordinates needed. "
        "ALWAYS try this before the mouse tool. Actions:\n"
        "  get_tree   — list all accessible elements in the app window\n"
        "  click      — click a button or element by its title\n"
        "  click_menu — click a menu item, e.g. File → Save\n"
        "  type       — type text (clipboard-paste, Unicode-safe)\n"
        "  press_key  — press a key or chord: key='return', modifiers=['command']\n"
        "  get_value  — read the current text value of a named element"
    )

    class Params(Tool.Params):
        action: Literal["get_tree", "click", "click_menu", "type", "press_key", "get_value"] = Field(
            description="UI action to perform."
        )
        app: str = Field(
            description=(
                "Application name / process name. "
                "macOS: process name as in Activity Monitor, e.g. 'TextEdit', 'Safari'. "
                "Linux: process or window title, e.g. 'gedit', 'firefox'. "
                "Windows: executable name or title, e.g. 'Notepad', 'notepad.exe'."
            )
        )
        title: Optional[str] = Field(default=None, description="Title or label of the UI element.")
        role: Optional[str] = Field(
            default=None,
            description=(
                "Element role to narrow the search. "
                "macOS: 'button', 'text field', 'text area', 'checkbox'. "
                "Linux: 'push button', 'entry', 'text', 'check box'. "
                "Windows: 'Button', 'Edit', 'Text', 'CheckBox', 'ComboBox'."
            ),
        )
        text: Optional[str] = Field(default=None, description="Text to type. Required for action='type'.")
        menu: Optional[str] = Field(
            default=None,
            description="Menu bar menu name for click_menu, e.g. 'File', 'Edit'.",
        )
        menu_item: Optional[str] = Field(
            default=None,
            description="Menu item name for click_menu, e.g. 'Save', 'Copy'.",
        )
        key: Optional[str] = Field(
            default=None,
            description=(
                "Key for press_key: 'return', 'escape', 'tab', 'space', 'delete', "
                "'up', 'down', 'left', 'right', 'home', 'end', 'f1'–'f12', or a single char."
            ),
        )
        modifiers: List[str] = Field(
            default_factory=list,
            description=(
                "Modifier keys for press_key: 'command' (macOS), 'ctrl', 'shift', 'alt'. "
                "E.g. ['command'] for cmd+key."
            ),
        )
        window_index: int = Field(default=1, description="Window index (1 = frontmost).")

    async def execute(self, ctx: ToolContext, params: "UITool.Params") -> ToolResult:
        from opendesk.computer.sandbox import ActionType, get_sandbox

        await ctx.check_permission(
            tool="ui", argument=f"{params.action} in {params.app}",
            description=f"UI accessibility action: {params.action} in '{params.app}'",
        )

        sandbox = get_sandbox(ctx.session_id)

        _summary = {"action": params.action, "app": params.app, "title": params.title}
        _replay_p = {
            "action": params.action, "app": params.app, "title": params.title,
            "role": params.role, "text": params.text, "menu": params.menu,
            "menu_item": params.menu_item, "key": params.key, "modifiers": params.modifiers,
            "window_index": params.window_index,
        }

        try:
            result_msg = await self._dispatch(ctx, params)
        except (RuntimeError, ValueError, ImportError, NotImplementedError) as exc:
            await sandbox.record_action(
                ActionType.UI_ACTION, _summary, error=str(exc), replay_params=_replay_p,
            )
            return ToolResult(
                title=f"UI error: {params.action} in {params.app}",
                output=str(exc),
                error=True,
            )
        except Exception as exc:
            return ToolResult(title=f"UI error: {params.action}", output=str(exc), error=True)

        await sandbox.record_action(
            ActionType.UI_ACTION, _summary, result=result_msg[:200], replay_params=_replay_p,
        )
        label = params.title or params.menu_item or params.key or ""
        return ToolResult(
            title=f"UI: {params.action} '{label}' in {params.app}",
            output=result_msg,
        )

    async def _dispatch(self, ctx: ToolContext, params: "UITool.Params") -> str:
        comp = ctx.computer

        if params.action == "get_tree":
            tree = await comp.ui_tree(app=params.app)
            text = _format_tree(tree)
            return text or (
                f"No accessible elements in '{params.app}'. App may use custom "
                "rendering (Electron, games). Fall back to mouse tool with "
                "image_width/image_height."
            )

        if params.action == "click":
            if not params.title and not params.role:
                raise ValueError("Provide at least 'title' or 'role' for click.")
            tree = await comp.ui_tree(app=params.app)
            element = _find_element(tree, title=params.title, role=params.role)
            if element is None:
                raise RuntimeError(
                    f"No element title={params.title!r} role={params.role!r} in "
                    f"'{params.app}'. Run get_tree to see available elements."
                )
            used_a11y = await _try_perform_action(comp, element, "click", params.app)
            if not used_a11y:
                if element.bounds is None:
                    raise RuntimeError(
                        f"Element '{element.name}' has no bounds and no native a11y action — cannot click."
                    )
                await comp.click(element.bounds.center)
            how = "a11y" if used_a11y else "bounds-center"
            return f"Clicked [{element.role}] {element.name!r} in {params.app} (via {how})."

        if params.action == "click_menu":
            if not params.menu or not params.menu_item:
                raise ValueError("Both 'menu' and 'menu_item' are required for click_menu.")
            await comp.focus_app(params.app)
            synth = UIElement(
                role="menu item",
                name=params.menu_item,
                metadata={"menu_path": [params.menu, params.menu_item], "app": params.app},
            )
            used_a11y = await _try_perform_action(comp, synth, "click", params.app)
            if used_a11y:
                return f"Clicked {params.menu} → {params.menu_item} in {params.app} (via a11y)."

            tree = await comp.ui_tree(app=params.app)
            menu_el = _find_element(tree, title=params.menu, role=None)
            if menu_el is None or menu_el.bounds is None:
                raise RuntimeError(
                    f"Menu '{params.menu}' not found in {params.app}. Run get_tree to inspect."
                )
            await comp.click(menu_el.bounds.center)
            await asyncio.sleep(0.3)
            tree2 = await comp.ui_tree(app=params.app)
            item_el = _find_element(tree2, title=params.menu_item, role=None)
            if item_el is None or item_el.bounds is None:
                raise RuntimeError(
                    f"Menu item '{params.menu_item}' not found after opening menu '{params.menu}'."
                )
            await comp.click(item_el.bounds.center)
            return f"Clicked {params.menu} → {params.menu_item} in {params.app} (via bounds)."

        if params.action == "type":
            if not params.text:
                raise ValueError("'text' is required for action='type'.")
            await comp.focus_app(params.app)
            await asyncio.sleep(0.2)
            await comp.type_text(params.text)
            preview = params.text[:60] + ("…" if len(params.text) > 60 else "")
            return f"Typed {len(params.text)} chars into {params.app}: {preview!r}"

        if params.action == "press_key":
            if not params.key:
                raise ValueError("'key' is required for action='press_key'.")
            await comp.focus_app(params.app)
            await asyncio.sleep(0.15)
            await comp.press(params.key, modifiers=_to_modifiers(params.modifiers))
            combo = ("+".join(params.modifiers) + "+" if params.modifiers else "") + params.key
            return f"Pressed {combo} in {params.app}."

        if params.action == "get_value":
            if not params.title and not params.role:
                raise ValueError("Provide 'title' or 'role' for get_value.")
            tree = await comp.ui_tree(app=params.app)
            element = _find_element(tree, title=params.title, role=params.role)
            if element is None:
                raise RuntimeError(
                    f"No element title={params.title!r} role={params.role!r} in {params.app}."
                )
            return element.value or element.name or "(no value)"

        raise ValueError(f"Unknown action: {params.action!r}")
