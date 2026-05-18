# `clipboard` — Clipboard Read/Write

```python
from opendesk.tools.clipboard import ClipboardTool
tool = ClipboardTool()
```

## Actions

| Action | Parameters | Description |
|--------|-----------|-------------|
| `read` | — | Return current clipboard text |
| `write` | `text` | Set clipboard text |

## Examples

```python
params = ClipboardTool.Params

# Read
result = await tool.execute(ctx, params(action="read"))
print(result.output)

# Write, then paste
await tool.execute(ctx, params(action="write", text="Hello from opendesk"))
# Then use keyboard tool to paste with Ctrl/Cmd+V
```

---

Next: [ocr →](ocr.md) — extract text from any region without the clipboard.
