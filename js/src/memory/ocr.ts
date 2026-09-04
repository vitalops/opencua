/**
 * OCR engines for the screen-memory daemon.
 *
 * Backends
 * --------
 * - `vision`    macOS Vision framework via a tiny Swift helper compiled once
 *               with `swiftc` to `<home>/bin/vision-ocr-v1` (the same binary
 *               the Python SDK builds, so the two share it).  Offline, ~0.3 s.
 * - `winrt`     Windows.Media.Ocr via PowerShell.  Offline.
 * - `tesseract` tesseract.js with a persistent worker.  Cross-platform; on
 *               first use it downloads the English model (~10 MB) and caches
 *               it under `<home>/memory/tessdata` so later runs are offline.
 *
 * `auto` prefers the native engine on macOS / Windows and tesseract.js
 * elsewhere.
 */

import { execFile } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { memoryDir, resolveHomeDir } from "./config.js";

const exec = promisify(execFile);

export interface OcrEngine {
  readonly backend: string;
  recognize(png: Buffer): Promise<string>;
  close(): Promise<void>;
}

export type OcrPreference = "auto" | "native" | "vision" | "winrt" | "tesseract";

// Keep byte-for-byte in sync with python/opendesk/computer/ocr.py so both
// SDKs can reuse one compiled helper.
const VISION_SWIFT_SRC = `
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
`;

const VISION_HELPER_VERSION = "1";

function which(bin: string): boolean {
  const dirs = (process.env["PATH"] ?? "").split(path.delimiter);
  return dirs.some((d) => d && fs.existsSync(path.join(d, bin)));
}

export function visionHelperPath(home?: string): string {
  return path.join(resolveHomeDir(home), "bin", `vision-ocr-v${VISION_HELPER_VERSION}`);
}

/** Compile the Vision helper once with swiftc.  Returns its path or null. */
export async function ensureVisionHelper(home?: string): Promise<string | null> {
  if (process.platform !== "darwin") return null;
  const bin = visionHelperPath(home);
  if (fs.existsSync(bin)) return bin;
  if (!which("swiftc")) return null;
  const dir = path.dirname(bin);
  fs.mkdirSync(dir, { recursive: true });
  const src = path.join(dir, "vision-ocr.swift");
  try {
    fs.writeFileSync(src, VISION_SWIFT_SRC);
    await exec("swiftc", ["-O", "-o", bin, src], { timeout: 180_000 });
    fs.chmodSync(bin, 0o700);
    return fs.existsSync(bin) ? bin : null;
  } catch {
    return null;
  } finally {
    fs.rmSync(src, { force: true });
  }
}

async function withTempPng<T>(png: Buffer, fn: (p: string) => Promise<T>): Promise<T> {
  const p = path.join(os.tmpdir(), `opendesk-ocr-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}.png`);
  fs.writeFileSync(p, png, { mode: 0o600 });
  try {
    return await fn(p);
  } finally {
    fs.rmSync(p, { force: true });
  }
}

class VisionEngine implements OcrEngine {
  readonly backend = "macos-vision";
  constructor(private helper: string) {}
  async recognize(png: Buffer): Promise<string> {
    return withTempPng(png, async (p) => {
      const { stdout } = await exec(this.helper, [p], { timeout: 30_000, maxBuffer: 8 * 1024 * 1024 });
      return stdout.trim();
    });
  }
  async close(): Promise<void> { /* nothing to release */ }
}

class WinRtEngine implements OcrEngine {
  readonly backend = "windows-winrt";
  async recognize(png: Buffer): Promise<string> {
    return withTempPng(png, async (p) => {
      const ps = p.replace(/\\/g, "/");
      const script = `
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType=WindowsRuntime] | Out-Null
$file = [Windows.Storage.StorageFile]::GetFileFromPathAsync('${ps}').AsTask().Result
$stream = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read).AsTask().Result
$decoder = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream).AsTask().Result
$bitmap = $decoder.GetSoftwareBitmapAsync().AsTask().Result
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = $engine.RecognizeAsync($bitmap).AsTask().Result
$result.Lines | ForEach-Object { $_.Text }
`;
      const { stdout } = await exec("powershell", ["-NonInteractive", "-Command", script], { timeout: 30_000, maxBuffer: 8 * 1024 * 1024 });
      return stdout.trim();
    });
  }
  async close(): Promise<void> { /* nothing */ }
}

class TesseractEngine implements OcrEngine {
  readonly backend = "tesseract.js";
  private worker: { recognize(img: Buffer): Promise<{ data: { text: string } }>; terminate(): Promise<unknown> } | null = null;
  constructor(private cacheDir: string) {}
  private async ensure() {
    if (this.worker) return this.worker;
    const { createWorker } = await import("tesseract.js");
    fs.mkdirSync(this.cacheDir, { recursive: true });
    // tesseract.js v5: createWorker(langs, oem, options)
    this.worker = await (createWorker as unknown as (
      langs: string, oem?: number, opts?: Record<string, unknown>,
    ) => Promise<typeof this.worker>)("eng", 1, { cachePath: this.cacheDir, logger: () => {} });
    return this.worker!;
  }
  async recognize(png: Buffer): Promise<string> {
    const w = await this.ensure();
    const { data } = await w.recognize(png);
    return data.text.trim();
  }
  async close(): Promise<void> {
    if (this.worker) {
      try { await this.worker.terminate(); } catch { /* ignore */ }
      this.worker = null;
    }
  }
}

/** Which backend `createOcrEngine` would pick, without creating it. */
export function availableBackend(pref: OcrPreference = "auto", home?: string): string | null {
  const nativeMac = process.platform === "darwin" && (fs.existsSync(visionHelperPath(home)) || which("swiftc"));
  const nativeWin = process.platform === "win32" && which("powershell.exe");
  const tess = fs.existsSync(path.join(resolveNodeModules(), "tesseract.js"));
  if (pref === "vision") return nativeMac ? "macos-vision" : null;
  if (pref === "winrt") return nativeWin ? "windows-winrt" : null;
  if (pref === "native") return nativeMac ? "macos-vision" : nativeWin ? "windows-winrt" : null;
  if (pref === "tesseract") return tess ? "tesseract.js" : null;
  if (nativeMac) return "macos-vision";
  if (nativeWin) return "windows-winrt";
  return tess ? "tesseract.js" : null;
}

function resolveNodeModules(): string {
  // Walk up from this file to find node_modules (works from src/ and dist/).
  let dir = path.dirname(new URL(import.meta.url).pathname);
  for (let i = 0; i < 6; i++) {
    const nm = path.join(dir, "node_modules");
    if (fs.existsSync(nm)) return nm;
    dir = path.dirname(dir);
  }
  return "node_modules";
}

/** Build the preferred engine, or null when none is usable. */
export async function createOcrEngine(pref: OcrPreference = "auto", home?: string): Promise<OcrEngine | null> {
  const backend = availableBackend(pref, home);
  if (backend === "macos-vision") {
    const helper = await ensureVisionHelper(home);
    if (helper) return new VisionEngine(helper);
    if (pref !== "auto") return null;
  }
  if (backend === "windows-winrt") return new WinRtEngine();
  if (backend === "tesseract.js" || (pref === "auto" && backend !== null)) {
    return new TesseractEngine(path.join(memoryDir(home), "tessdata"));
  }
  return null;
}
