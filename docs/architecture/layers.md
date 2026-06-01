# Computer & Tools Layers

## Layer 1: `computer/`

### `Computer` ABC (`base.py`)

The capability surface of a computer. Three kinds of operations:

* **Observe** (one-shot queries): `capture`, `cursor_position`, `displays`,
  `windows`, `focused_window`, `ui_tree`, `clipboard_read`, `processes`,
  `environment`, `read_file`, `list_dir`, `stat`, `notifications`.
* **Act** (one-shot state changes): `pointer`, `key`, `text`, `open_app`,
  `close_app`, `focus_app`, `focus_window`, `move_window`, `close_window`,
  `perform_ui_action`, `clipboard_write`, `write_file`, `delete`, `move`,
  `mkdir`, `shell`, `exec`, `lock_screen`.
* **Subscribe** (server-pushed streams): `subscribe_display`,
  `subscribe_input` — return async iterators.

Plus a sync `capabilities()` returning a `CapabilityManifest` so callers can
check support before attempting an operation.

Convenience helpers on top of the abstract primitives: `click`, `drag`,
`scroll`, `press`, `hotkey`, `type_text`, `clipboard_text` / `clipboard_set_text`.

### `LocalComputer` (`local.py`)

Concrete implementation for the current machine. Wraps the existing screen
capture (mss), accessibility backends (AppleScript / AT-SPI2 / UI Automation),
input (pyautogui), and filesystem / process primitives. All blocking I/O
runs in `asyncio.to_thread`.

### `RemoteComputer` (`remote.py`)

Implements the same `Computer` ABC by forwarding every call to a
`Peer`. Each abstract method serialises params, awaits
`peer.call(method, params)`, and validates the result back into a Pydantic
model. Subscriptions return async iterators backed by `peer.stream(...)`.

`capabilities()` is synchronous (per the ABC) and served from the manifest
cached during the HELLO handshake — no round-trip.

### `ComputerDispatcher` (`dispatcher.py`)

Server-side router. Implements the protocol's `Dispatcher` Protocol by
mapping each method name (`display.capture`, `input.pointer`, …) to the
matching `Computer` method, with Pydantic de/serialisation at the boundary.

### `types.py`

Pydantic value types exchanged across the boundary: `Point`, `Rect`,
`Pixmap` (with built-in pixel ↔ logical coordinate translation), `Display`,
`Window`, `Process`, `UIElement`, `PointerEvent`, `KeyEvent`,
`ClipboardContents`, `FileEntry`, `CompletedCommand`, `Environment`,
`Notification`, the `Capability` enum, and the `CapabilityManifest`.

### Auxiliary modules

* `capture.py` — mss-based screen capture. Wider-than-1920 displays are
  downscaled; the resulting `Pixmap` always carries the true logical screen
  dimensions so coordinates translate cleanly.
* `marks.py` — Set-of-Marks rendering helpers (`draw_som_marks`,
  `overlay_cursor`). The element list is now produced by `Computer.ui_tree()`
  rather than by a separate accessibility query.
* `ocr.py` — `ocr_image(png_bytes)` runs the best available OCR backend
  (pytesseract → macOS Vision → WinRT OCR). Decoupled from capture so it
  works on a `Pixmap` from any Computer, including a remote one.
* `sandbox.py` — per-session audit log used by tools that record actions.

---

## Computer Use: Three-Tier Grounding Architecture

All computer-use interaction follows a strict three-tier hierarchy. The tier
priority is encoded directly in tool descriptions, so any model that reads
the tool list follows it without a separate orchestration layer.

```
Tier 1: Semantic AX  ──(element not found)──►  Tier 2: Grounded SoM  ──(AX tree empty)──►  Tier 3: Raw Pixel
```

Every action at any tier is followed by an automatic per-action pixel-diff
against the prior screenshot — a free feedback signal that tells the model
whether its action had any visual effect.

### Tier 1 — Semantic Accessibility Grounding

The `ui` tool finds elements by `title` and `role` via the platform
accessibility API and acts on them directly. No pixel coordinates are
involved at any point.

The tool performs a depth-first search over `Computer.ui_tree()`, matching
case-insensitively and by substring. When a match is found:

- **macOS**: `click elem` via System Events AppleScript, with a bounds clause
  to disambiguate multiple controls sharing a label.
- **Linux**: `doAction("click")` via AT-SPI2.
- **Windows**: `click_input()` via UI Automation / pywinauto.

If the backend does not support native actions, the tool falls back to a
pointer click at `element.bounds.center` — still Tier 1, because the
coordinate comes from the accessibility tree, not from the model.

**Key property**: resolution-independent. Element positions are in logical
pixels from the OS and are invariant to DPI, window position, and app version.

### Tier 2 — Grounded Set-of-Marks (SoM)

When Tier 1 cannot find a named element but the AX tree is non-empty, the
model calls `screenshot(marks=True)`. The pipeline:

1. Capture screen via `mss`, downscale to ≤ 1920 px (LANCZOS), record
   scale factors `sx = W_img / W_logical`, `sy = H_img / H_logical`.
2. Query the same AX backend as Tier 1 for all interactive elements with
   their logical bounding boxes.
3. Project each bounding box into image pixels:
   `x_img = floor(x_logical × sx)`, `y_img = floor(y_logical × sy)`.
4. Render numbered, colored bounding-box chips onto the image via Pillow.
5. Return the annotated PNG **and** a text index:
   `[3] AXButton "Save" at (120,340) 80×30 — click mark 3 to interact`.

The model picks a mark by index. The system maps the index back to the
element's logical center and issues the click — no model coordinate
estimation occurs.

**Why "grounded" matters**: marks come from the AX tree, so each mark is a
platform-confirmed interactive control. False-positive rate is zero by
construction, unlike vision-detector-based SoM (e.g. OmniParser).

### Tier 3 — Raw Pixel Mouse Control

Used only when the AX tree is empty (games, canvas apps, custom-rendered
surfaces). The model reads coordinates from an unannotated screenshot and
passes `image_width` / `image_height` with every click. The mouse tool
normalizes those back to logical pixels before issuing the pointer event
(see Retina normalization below).

### Unified Element Schema

All three platform backends produce a flat list of dicts with a single schema:

```
{ mark, role, label, x, y, w, h }   ← all in logical screen pixels
```

The SoM renderer, element search, click dispatch, replay engine, and
pixel-diff feedback are all platform-agnostic above this contract.

| Platform | API | Element limit |
|---|---|---|
| macOS | AppleScript / System Events | 120 per window |
| Linux | AT-SPI2 (`pyatspi`) | 150, depth ≤ 8 |
| Windows | UI Automation (`pywinauto`) | 150 |

### Retina / HiDPI Coordinate Normalization

`mss` captures at physical resolution (e.g. 2880×1800 on a 2× Retina Mac).
The OS pointer API operates in logical pixels (e.g. 1440×900). Passing
physical-pixel image coordinates directly to the pointer API produces clicks
at 2× the intended position.

`Pixmap` carries four dimension fields: `width`, `height` (image after
downscaling), `logical_width`, `logical_height` (from OS display API).
Scale factors `sx` and `sy` are pre-computed and used in two places:

- **SoM rendering**: logical AX coordinates scaled up to image pixels for chip placement.
- **Mouse tool**: image coordinates from the model scaled back down:
  `x_logical = floor(x_img / sx)`, `y_logical = floor(y_img / sy)`.

A 2% no-op band skips scaling when image and logical dimensions effectively
match, avoiding rounding artefacts at unity scale.

### Per-Action Pixel-Diff Feedback

After every screenshot, `capture.py` pixel-diffs the new capture against the
previous one using a per-channel absolute-difference threshold of 10 intensity
units (to filter compression noise). The result is a structured report
appended to every screenshot tool output:

```
changed: bool
change_fraction: float       # 0.0–1.0
changed_region: [x, y, w, h] # bounding box of changed pixels
summary: str                  # natural-language signal for the model
```

Example outputs:
```
12.3% of pixels changed in region [x=400, y=200, 600×300px].
No visible change detected — the action may not have had any effect.
```

This closes the perception loop per-action, in tens of milliseconds, with no
VLM call. The model can immediately detect ineffective actions and re-plan
(retry via a different tier, adjust parameters, or check focus).

### Tier-Agnostic Action Replay

Every action — regardless of which tier executed it — is recorded with two
parameter dicts:

- `params` — human-readable summary for the audit display.
- `replay_params` — complete, platform-neutral parameter set for re-execution.

A Tier 1 (semantic AX) action records `{action, app, title, role, ...}`.
A Tier 2/3 (pixel) action records `{action, x, y, image_width, image_height, ...}`.

The replay engine re-issues the action through the same tool interface without
needing the original screenshot, the original AX tree, or the original
tier-selection decision.

---

## Layer 2: `tools/`

### `base.py`

```
ToolResult      — title, output, error, attachments, metadata
Attachment      — filename, content (bytes), media_type
ToolContext     — session_id, permission_handler, computer
Tool            — ABC: name, description, Params (Pydantic), execute()
```

The crucial field on `ToolContext` is **`computer: Computer`** — every tool
calls `ctx.computer.X(...)` instead of poking pyautogui / AppleScript
directly. Swap in a `RemoteComputer` and the same tool runs against another
machine with zero code changes.

`ToolContext.check_permission(tool, argument, description)` calls the
injected handler before every action. Raise `PermissionDeniedError` to block.

### Tool files

Each file contains one `Tool` subclass. None of them open subprocesses or
poke OS APIs directly — they always go through `ctx.computer`.

| File | Class | What it does |
|------|-------|-------------|
| `screenshot.py` | `ScreenshotTool` | `ctx.computer.capture()` + optional SoM marks via `ui_tree()` |
| `mouse.py` | `MouseTool` | `ctx.computer.click/drag/scroll` with image→logical coord translation |
| `keyboard.py` | `KeyboardTool` | `ctx.computer.text/press/hotkey` |
| `ui.py` | `UITool` | `ctx.computer.ui_tree()` + `perform_ui_action` (or bounds-center fallback) |
| `app.py` | `AppTool` | `ctx.computer.open_app/close_app/focus_app/list_apps` |
| `clipboard.py` | `ClipboardTool` | `ctx.computer.clipboard_read/clipboard_write` |
| `ocr.py` | `OCRTool` | `ctx.computer.capture()` → `ocr_image(pixmap.data)` |
| `automation.py` | `LearnTool`, `ScheduleTool` | Local session state; never remoted |
| `audit.py` | `AuditTool` | Local audit log; never remoted |

---

Next: [Protocol Layer →](protocol-layer.md)
