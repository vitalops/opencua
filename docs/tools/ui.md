# `ui` — Accessibility-based UI Interaction

The primary interaction tool. Clicks, types, and reads values using the platform's native accessibility API. No pixel coordinates needed.

```python
from opendesk.tools.ui import UITool
tool = UITool()
```

## Actions

| Action | Required params | Description |
|--------|----------------|-------------|
| `get_tree` | `app` | List all accessible elements in the app window |
| `click` | `app`, `title` or `role` | Click a button or element by its visible label |
| `click_menu` | `app`, `menu`, `menu_item` | Click a menu bar item: `File → Save` |
| `type` | `app`, `text` | Type text into the focused element (Unicode-safe) |
| `press_key` | `app`, `key` | Press a key or chord |
| `get_value` | `app`, `title` or `role` | Read the current text value of an element |

## Examples

```python
params = UITool.Params

# List what's in the window
await tool.execute(ctx, params(action="get_tree", app="TextEdit"))

# Click a button
await tool.execute(ctx, params(action="click", app="TextEdit", title="Save"))

# Open File menu → New
await tool.execute(ctx, params(action="click_menu", app="TextEdit", menu="File", menu_item="New"))

# Type text (clipboard-paste, full Unicode)
await tool.execute(ctx, params(action="type", app="TextEdit", text="Hello, 世界 🌍"))

# Press Cmd+S to save
await tool.execute(ctx, params(action="press_key", app="TextEdit", key="s", modifiers=["command"]))

# Read a text field value
await tool.execute(ctx, params(action="get_value", app="Safari", title="Address and Search Bar"))
```

## Platform notes

- **macOS**: uses AppleScript / System Events. App name must match Activity Monitor (e.g. `"Google Chrome"` not `"chrome"`).
- **Linux**: uses AT-SPI2 (`pyatspi`) with `xdotool` fallback for type/press_key.
- **Windows**: uses UI Automation with Win32 fallback. App name matches window title or process name.

When an app uses custom rendering (canvas apps, games, Electron), `get_tree` may return an empty tree — fall back to `screenshot(marks=True)` and the `mouse` tool.

---

Next: [screenshot →](screenshot.md)
