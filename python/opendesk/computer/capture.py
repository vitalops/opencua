"""Screen-capture helpers using *mss* and *Pillow*.

All functions are synchronous; run them in a thread pool when called from
async code.

Example::

    png_bytes, w, h = capture_screen()
    png_bytes, w, h = capture_screen(region=(100, 100, 800, 600))
    b64, w, h = capture_screen_b64()
    report = diff_screenshots(before_png, after_png)
"""

from __future__ import annotations

import base64
import io
import os
import platform
from typing import Any

_PLATFORM = platform.system()
_MAX_WIDTH = 1920  # downscale Retina / 4K screens to stay under API size limits


def capture_screen(
    region: tuple[int, int, int, int] | None = None,
) -> tuple[bytes, int, int]:
    """Capture a screenshot and return ``(png_bytes, width, height)``.

    Parameters
    ----------
    region:
        Optional ``(x, y, width, height)`` in *logical* screen coordinates.
        ``None`` captures the entire primary monitor.

    Raises
    ------
    ImportError
        When ``mss`` or ``Pillow`` are not installed.
    RuntimeError
        When the captured data length doesn't match expectations.  On macOS
        this usually means Screen Recording permission has not been granted
        (System Settings → Privacy & Security → Screen Recording).
    """
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "Pillow is required for screen capture: pip install 'opendesk[core]'"
        ) from exc

    # macOS fast path: the system `screencapture` tool uses ScreenCaptureKit
    # and returns in ~0.3 s, whereas mss's CGWindowListCreateImage path can
    # stall for ~30 s per grab on macOS 15+/26.  Falls back to mss on any
    # failure.  Set OPENDESK_CAPTURE=mss to force the mss path.
    if _PLATFORM == "Darwin" and os.environ.get("OPENDESK_CAPTURE", "").lower() != "mss":
        img = _macos_screencapture(region)
        if img is not None:
            return _encode(img)

    try:
        import mss  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "mss is required for screen capture: pip install 'opendesk[core]'"
        ) from exc

    with mss.mss() as sct:
        if region is not None:
            x, y, w, h = region
            monitor: dict[str, int] = {"left": x, "top": y, "width": w, "height": h}
        else:
            monitor = dict(sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0])

        sct_img = sct.grab(monitor)

        # mss returns a memoryview — materialise to bytes before PIL decode.
        # The "BGRX" raw decoder reorders channels B→R G→G R→B correctly.
        raw_bgra = bytes(sct_img.bgra)

        expected = sct_img.width * sct_img.height * 4
        if len(raw_bgra) != expected:
            raise RuntimeError(
                f"Screen capture data size mismatch: got {len(raw_bgra)} bytes, "
                f"expected {expected} ({sct_img.width}×{sct_img.height}×4 BGRA). "
                "On macOS: grant Screen Recording permission in System Settings → "
                "Privacy & Security → Screen Recording."
            )

        img = Image.frombytes(
            "RGB",
            (sct_img.width, sct_img.height),
            raw_bgra,
            "raw",
            "BGRX",
        )

    return _encode(img)


def _encode(img: "Any") -> tuple[bytes, int, int]:
    """Downscale to ``_MAX_WIDTH`` if needed and PNG-encode."""
    from PIL import Image  # type: ignore[import-not-found]

    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > _MAX_WIDTH:
        scale = _MAX_WIDTH / img.width
        new_h = max(1, int(img.height * scale))
        img = img.resize((_MAX_WIDTH, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    png_bytes = buf.getvalue()

    if not png_bytes:
        raise RuntimeError("PNG encoding produced empty output.")

    return png_bytes, img.width, img.height


def _macos_screencapture(
    region: tuple[int, int, int, int] | None,
) -> "Any | None":
    """Capture the main display via ``/usr/sbin/screencapture``.

    Returns a PIL image, or ``None`` when the tool is missing or fails (the
    caller then falls back to mss).  ``-x`` silences the shutter sound,
    ``-D 1`` selects the main display, ``-R`` takes logical coordinates.
    """
    import subprocess
    import tempfile

    from PIL import Image  # type: ignore[import-not-found]

    exe = "/usr/sbin/screencapture"
    if not os.path.exists(exe):
        return None
    cmd = [exe, "-x", "-D", "1", "-t", "png"]
    if region is not None:
        x, y, w, h = region
        cmd += ["-R", f"{x},{y},{w},{h}"]
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        r = subprocess.run(cmd + [tmp], capture_output=True, timeout=15)
        if r.returncode != 0 or not os.path.getsize(tmp):
            return None
        with Image.open(tmp) as im:
            im.load()
            return im.convert("RGB")
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def capture_screen_b64(
    region: tuple[int, int, int, int] | None = None,
) -> tuple[str, int, int]:
    """Like :func:`capture_screen` but returns base-64 encoded PNG."""
    png_bytes, w, h = capture_screen(region)
    return base64.b64encode(png_bytes).decode("ascii"), w, h


def screen_size() -> tuple[int, int]:
    """Return ``(width, height)`` of the primary monitor in logical pixels."""
    try:
        import mss  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError("mss is required: pip install 'opendesk[core]'") from exc

    with mss.mss() as sct:
        m = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        return m["width"], m["height"]


def diff_screenshots(
    before_png: bytes,
    after_png: bytes,
    threshold: int = 10,
) -> dict[str, object]:
    """Compare two PNG screenshots and return a change report.

    Parameters
    ----------
    before_png, after_png:
        Raw PNG bytes from :func:`capture_screen`.
    threshold:
        Per-channel intensity delta (0–255) below which a pixel is considered
        unchanged.  Default 10 filters camera / compression noise.

    Returns
    -------
    dict with keys:
        ``changed``         — bool
        ``change_fraction`` — float 0.0–1.0
        ``changed_region``  — ``[x, y, w, h]`` bounding box or ``None``
        ``summary``         — human-readable string for the LLM
    """
    try:
        from PIL import Image, ImageChops, ImageStat  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "Pillow is required for screenshot diffing: pip install 'opendesk[core]'"
        ) from exc

    before = Image.open(io.BytesIO(before_png)).convert("RGB")
    after = Image.open(io.BytesIO(after_png)).convert("RGB")

    if before.size != after.size:
        return {
            "changed": True,
            "change_fraction": 1.0,
            "changed_region": None,
            "summary": f"Screen resolution changed from {before.size} to {after.size}.",
        }

    diff = ImageChops.difference(before, after)
    gray = diff.convert("L")
    mask = gray.point(lambda p: 255 if p > threshold else 0)
    changed_pixels = int(ImageStat.Stat(mask).sum[0] / 255)
    total = before.width * before.height
    fraction = changed_pixels / total if total > 0 else 0.0

    if fraction < 0.001:
        return {
            "changed": False,
            "change_fraction": fraction,
            "changed_region": None,
            "summary": "No visible change detected — the action may not have had any effect.",
        }

    bbox = mask.getbbox()
    changed_region = None
    region_str = ""
    if bbox:
        x, y, x2, y2 = bbox
        changed_region = [x, y, x2 - x, y2 - y]
        region_str = f" in region [x={x}, y={y}, {x2-x}×{y2-y}px]"

    summary = f"{fraction:.1%} of pixels changed{region_str}."
    return {
        "changed": True,
        "change_fraction": fraction,
        "changed_region": changed_region,
        "summary": summary,
    }
