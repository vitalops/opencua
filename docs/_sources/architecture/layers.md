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
