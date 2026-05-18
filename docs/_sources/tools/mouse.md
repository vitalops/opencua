# `mouse` — Mouse Control

```python
from opendesk.tools.mouse import MouseTool
tool = MouseTool()
```

**Always provide `image_width` and `image_height` from the screenshot tool.** This ensures correct coordinate translation on Retina / HiDPI displays.

## Actions

| Action | Parameters | Description |
|--------|-----------|-------------|
| `click` | `x, y` | Left-click |
| `double_click` | `x, y` | Double left-click |
| `triple_click` | `x, y` | Triple-click (select word/line) |
| `right_click` | `x, y` | Right-click |
| `middle_click` | `x, y` | Middle-click (open link in new tab) |
| `move` | `x, y` | Move without clicking |
| `scroll` | `x, y, direction, amount` | Scroll up/down/left/right |
| `drag` | `x, y, end_x, end_y` | Click-and-drag |
| `left_down` | `x, y` | Press and hold left button |
| `left_up` | `x, y` | Release left button |
| `cursor_position` | — | Return current cursor location |

## Examples

```python
params = MouseTool.Params

# Always get screenshot dimensions first
shot = await registry.get("screenshot").execute(ctx, ScreenshotTool.Params())
w = shot.metadata["width"]
h = shot.metadata["height"]

# Click at image coordinates (auto-scaled to logical coords)
await tool.execute(ctx, params(action="click", x=400, y=300, image_width=w, image_height=h))

# Scroll down
await tool.execute(ctx, params(action="scroll", x=760, y=400, direction="down", amount=5,
                               image_width=w, image_height=h))

# Drag
await tool.execute(ctx, params(action="drag", x=100, y=100, end_x=500, end_y=300,
                               image_width=w, image_height=h))
```

---

Next: [keyboard →](keyboard.md)
