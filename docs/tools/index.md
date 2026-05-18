# Tools Reference

All tools share the same interface: `await tool.execute(ctx, params) -> ToolResult`.

**Tool priority rule:** `ui` → `screenshot(marks=True)` → `mouse` with image dimensions.

| Tool | Description |
|---|---|
| [`ui`](ui.md) | Accessibility-based UI interaction — click by name, type, read values |
| [`screenshot`](screenshot.md) | Screen capture with optional Set-of-Marks overlay |
| [`mouse`](mouse.md) | Pixel-level mouse control with HiDPI coordinate translation |
| [`keyboard`](keyboard.md) | Type text, press keys, send hotkeys |
| [`app`](app.md) | Open, close, focus, and list applications |
| [`clipboard`](clipboard.md) | Read and write clipboard text |
| [`ocr`](ocr.md) | Extract text from any screen region |
| [`audit`](audit.md) | Read the session audit log |
| [`learn`](learn.md) | Record and replay desktop workflows |

---

## ToolResult

All tools return:

```python
@dataclass
class ToolResult:
    title: str           # short human-readable label
    output: str          # text returned to the LLM
    error: bool          # True if the action failed
    attachments: list[Attachment]  # binary files (screenshots, etc.)
    metadata: dict       # extra data for programmatic consumers
```

`Attachment`:

```python
@dataclass
class Attachment:
    filename: str
    content: bytes
    media_type: str      # e.g. "image/png"

    def to_base64(self) -> str: ...
```

---

Start with the primary interaction tool: [ui →](ui.md)
