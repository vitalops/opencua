# `screenshot` — Screen Capture

```python
from opendesk.tools.screenshot import ScreenshotTool
tool = ScreenshotTool()
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `marks` | bool | false | Overlay numbered bounding boxes (Set-of-Marks) on interactive elements |
| `show_cursor` | bool | false | Draw a red dot at the current cursor position |
| `zoom` | `[x0,y0,x1,y1]` | null | Crop a region for close-up inspection |
| `region` | `[x,y,w,h]` | null | Capture only this screen region |
| `save_path` | str | null | Save PNG to disk at this absolute path |

## Ask Claude

When opendesk is connected via MCP, just describe what you want:

> "Take a screenshot and tell me what's on my screen"
> "Take a screenshot with numbered marks on every button"
> "Show me what's in the top-right corner of the screen"
> "Save a screenshot to /tmp/before.png"

---

## SDK examples

```python
params = ScreenshotTool.Params

# Full screenshot
result = await tool.execute(ctx, params())
png = result.attachments[0].content  # bytes

# With SoM overlay — model can say "click mark 3"
result = await tool.execute(ctx, params(marks=True))
print(result.output)  # lists: [1] Button "OK" at (120,340) 80×30 — click mark 1 to interact

# Zoom into a region
result = await tool.execute(ctx, params(zoom=[100, 200, 400, 350]))

# Save to disk
result = await tool.execute(ctx, params(save_path="/tmp/before.png"))
```

## Output

The tool output always includes:
- Capture dimensions and Retina note: `image_width=1440, image_height=900` to pass to the mouse tool.
- Change detection vs previous screenshot: `"12.3% of pixels changed in region [x=400, y=200, 600×300px]"`.
- SoM summary if `marks=True`.

---

Next: [mouse →](mouse.md) — once you have screenshot dimensions, pass them here for pixel-accurate clicks.
