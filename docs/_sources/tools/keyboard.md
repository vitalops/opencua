# `keyboard` — Keyboard Input

```python
from opendesk.tools.keyboard import KeyboardTool
tool = KeyboardTool()
```

## Actions

| Action | Required | Description |
|--------|----------|-------------|
| `type` | `text` | Type text (clipboard-paste, full Unicode including CJK and emoji) |
| `press` | `key` | Press a named key: `enter`, `escape`, `tab`, `f5`, etc. |
| `hotkey` | `keys` | Send a key combination: `["ctrl","c"]`, `["command","s"]` |
| `hold` | `key`, `hold_duration` | Hold a key for N seconds then release |

## Examples

```python
params = KeyboardTool.Params

# Type text
await tool.execute(ctx, params(action="type", text="Hello, 世界! 🌍"))

# Press Enter
await tool.execute(ctx, params(action="press", key="enter"))

# Copy (Ctrl+C)
await tool.execute(ctx, params(action="hotkey", keys=["ctrl", "c"]))

# Cmd+Shift+P (VS Code command palette on macOS)
await tool.execute(ctx, params(action="hotkey", keys=["command", "shift", "p"]))

# Hold Shift for 0.5s (e.g. while clicking for range select)
await tool.execute(ctx, params(action="hold", key="shift", hold_duration=0.5))
```

Key names follow pyautogui conventions: `enter`, `escape`, `tab`, `backspace`, `delete`, `up`, `down`, `left`, `right`, `home`, `end`, `pageup`, `pagedown`, `f1`–`f12`, `ctrl`, `alt`, `shift`, `cmd`/`win`, etc.

---

Next: [app →](app.md)
