# `ocr` — Text Extraction

```python
from opendesk.tools.ocr import OCRTool
tool = OCRTool()
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `region` | `[x,y,w,h]` | null | Screen region to OCR; null = full screen |

## Ask Claude

> "Read the text on my screen"

> "What does that error message say?"

> "Extract the text from the dialog box"

> "Read the text in the top half of the screen"

---

## SDK examples

```python
params = OCRTool.Params

# Full screen
result = await tool.execute(ctx, params())
print(result.output)

# Specific region (e.g. a dialog box)
result = await tool.execute(ctx, params(region=[400, 200, 600, 300]))
```

Backends tried in order: `pytesseract` → macOS Vision (macOS only) → Windows WinRT (Windows only) → install hint if none available.

---

Next: [audit →](audit.md) — inspect everything the agent has done this session.
