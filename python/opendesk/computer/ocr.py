"""Screen-region OCR — extract visible text from any screen area.

Backends (tried in order)
--------------------------
1. ``pytesseract`` — best quality, cross-platform; needs Tesseract binary.
2. macOS Vision framework — zero extra deps on macOS 11+, via Swift subprocess.
3. Windows WinRT OCR — zero extra deps on Windows 10+, via PowerShell.
4. Graceful failure with clear install hints.

Usage::

    from opendesk.computer.ocr import extract_text_from_region

    # region = (x, y, width, height) in logical pixels; None = full screen
    text = extract_text_from_region(region=(100, 200, 800, 400))
"""

from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

_PLATFORM = platform.system()


def ocr_image(png_bytes: bytes, *, width: int = 0, height: int = 0) -> str:
    """Run OCR on a PNG byte buffer using the best available backend.

    Tries pytesseract, then platform-native OCR (Vision on macOS, WinRT on
    Windows).  Returns the extracted text or an informative error/install-hint
    string starting with ``"OCR "`` so callers can distinguish.

    ``width`` / ``height`` are optional; when set and small, the image is
    upscaled for better OCR accuracy.
    """
    try:
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
        img = Image.open(io.BytesIO(png_bytes))
        w = width or img.width
        h = height or img.height
        if 0 < w < 300:
            factor = max(2, 300 // w)
            img = img.resize((w * factor, h * factor), Image.LANCZOS)
        text = pytesseract.image_to_string(img, config="--psm 6")
        return text.strip() or "(no text detected)"
    except ImportError:
        pass
    except Exception as exc:
        return f"pytesseract error: {exc}"

    if _PLATFORM == "Darwin":
        try:
            return _macos_vision_ocr(png_bytes)
        except Exception:
            pass

    if _PLATFORM == "Windows":
        try:
            return _windows_winrt_ocr(png_bytes)
        except Exception:
            pass

    return (
        "OCR not available. Install pytesseract:\n"
        "  macOS:   brew install tesseract && pip install pytesseract\n"
        "  Ubuntu:  sudo apt install tesseract-ocr && pip install pytesseract\n"
        "  Windows: choco install tesseract && pip install pytesseract\n\n"
        "(On macOS 11+ and Windows 10+ a built-in OCR engine is also tried "
        "automatically without any extra installs.)"
    )


def available_backend() -> str | None:
    """Return the name of the OCR backend :func:`ocr_image` would use, or
    ``None`` when none is usable.  Cheap: no image is processed."""
    try:
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]  # noqa: F401
        try:
            pytesseract.get_tesseract_version()
            return "pytesseract"
        except Exception:
            pass
    except ImportError:
        pass
    if _PLATFORM == "Darwin":
        if shutil.which("swiftc") or shutil.which("swift"):
            return "macos-vision"
    if _PLATFORM == "Windows":
        if shutil.which("powershell"):
            return "windows-winrt"
    return None


def warm_up() -> None:
    """Prepare the native backend ahead of time (compiles the macOS Vision
    helper on first use, which can take a minute).  No-op elsewhere."""
    if _PLATFORM == "Darwin":
        _compiled_vision_helper()


def extract_text_from_region(
    region: tuple[int, int, int, int] | None = None,
) -> str:
    """Capture a screen region and run OCR on it.

    Convenience wrapper that uses :func:`opendesk.computer.capture.capture_screen`
    plus :func:`ocr_image`.  Prefer calling them separately when integrating
    with a :class:`~opendesk.computer.Computer` so OCR can run on a captured
    :class:`~opendesk.computer.Pixmap` from any backend.
    """
    try:
        from opendesk.computer.capture import capture_screen
        png_bytes, w, h = capture_screen(region)
    except Exception as exc:
        return f"OCR error: could not capture screen: {exc}"
    return ocr_image(png_bytes, width=w, height=h)


_VISION_SWIFT_SRC = """
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count > 1 else { exit(2) }
let url = URL(fileURLWithPath: args[1])
guard let img = NSImage(contentsOf: url),
      let cgImg = img.cgImage(forProposedRect: nil, context: nil, hints: nil)
else { exit(0) }

let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = true
let handler = VNImageRequestHandler(cgImage: cgImg, options: [:])
try? handler.perform([req])
let lines = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
print(lines.joined(separator: "\\n"))
"""

_VISION_HELPER_VERSION = "1"


def _vision_helper_dir() -> Path:
    override = os.environ.get("OPENDESK_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".opendesk"
    d = base / "bin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _compiled_vision_helper() -> Path | None:
    """Compile the Vision OCR helper once with ``swiftc`` and cache the
    binary under ``~/.opendesk/bin``.  Returns ``None`` if compilation
    isn't possible (no ``swiftc``), in which case callers fall back to
    interpreting the script with ``swift`` on every call."""
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return None
    d = _vision_helper_dir()
    binary = d / f"vision-ocr-v{_VISION_HELPER_VERSION}"
    if binary.exists() and os.access(binary, os.X_OK):
        return binary
    src = d / "vision-ocr.swift"
    try:
        src.write_text(_VISION_SWIFT_SRC)
        r = subprocess.run(
            [swiftc, "-O", "-o", str(binary), str(src)],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode != 0 or not binary.exists():
            return None
        os.chmod(binary, 0o700)
        return binary
    except Exception:
        return None
    finally:
        src.unlink(missing_ok=True)


def _macos_vision_ocr(png_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_bytes)
        tmp_png = Path(f.name)

    try:
        helper = _compiled_vision_helper()
        if helper is not None:
            r = subprocess.run(
                [str(helper), str(tmp_png)], capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                return r.stdout.strip() or "(no text detected)"
            # Fall through to the interpreted path on unexpected failure.

        swift_src = _VISION_SWIFT_SRC
        with tempfile.NamedTemporaryFile(suffix=".swift", delete=False, mode="w") as sf:
            sf.write(swift_src)
            swift_path = Path(sf.name)
        try:
            r = subprocess.run(
                ["swift", str(swift_path), str(tmp_png)],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0:
                return r.stdout.strip() or "(no text detected)"
            raise RuntimeError(f"Swift Vision OCR failed: {r.stderr.strip()}")
        finally:
            swift_path.unlink(missing_ok=True)
    finally:
        tmp_png.unlink(missing_ok=True)


def _windows_winrt_ocr(png_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_bytes)
        tmp_png = Path(f.name)

    tmp_ps = str(tmp_png).replace("\\", "/")

    ps_script = f"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType=WindowsRuntime] | Out-Null

$filePath = '{tmp_ps}'
$file = [Windows.Storage.StorageFile]::GetFileFromPathAsync($filePath).AsTask().Result
$stream = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read).AsTask().Result
$decoder = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream).AsTask().Result
$bitmap = $decoder.GetSoftwareBitmapAsync().AsTask().Result
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = $engine.RecognizeAsync($bitmap).AsTask().Result
$result.Lines | ForEach-Object {{ $_.Text }}
"""
    try:
        r = subprocess.run(
            ["powershell", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=25,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        raise RuntimeError(f"WinRT OCR failed: {r.stderr.strip()}")
    finally:
        tmp_png.unlink(missing_ok=True)
