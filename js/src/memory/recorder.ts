/**
 * Background capture loop for screen memory — mirrors Python's
 * opendesk/memory/recorder.py.
 *
 * Every `interval_seconds` the recorder:
 *   1. checks the shared pause flag (hotkey / tool / CLI),
 *   2. reads the frontmost app + window title and consults the deny list,
 *   3. grabs the screen,
 *   4. skips the frame when it is visually identical to the previous one,
 *   5. runs OCR locally, builds a JPEG thumbnail, and stores both,
 *   6. periodically enforces retention and the storage cap.
 *
 * Nothing leaves the machine.
 */

import {
  MemoryConfig,
  clearDaemonState,
  clearPause,
  getPause,
  loadConfig,
  setPause,
  writeDaemonState,
} from "./config.js";
import { frontmost as detectFrontmost, type Frontmost } from "./frontmost.js";
import { frameSignature, makeThumbnail, signatureDelta } from "./image.js";
import { availableBackend, createOcrEngine, ensureVisionHelper, type OcrEngine, type OcrPreference } from "./ocr.js";
import { MemoryStore } from "./store.js";

export type CaptureFn = () => Promise<Buffer>;
export type OcrFn = (png: Buffer) => Promise<string>;
export type FrontmostFn = () => Promise<Frontmost>;

export interface RecorderOptions {
  home?: string;
  config?: MemoryConfig;
  store?: MemoryStore;
  /** Screen capture; defaults to screenshot-desktop. */
  capture?: CaptureFn;
  /** OCR; defaults to the best local engine (see ocr.ts). */
  ocr?: OcrFn;
  /** Frontmost app/title probe; defaults to the platform probe. */
  frontmost?: FrontmostFn;
  /** CLI override that survives config reloads. */
  intervalOverride?: number;
  log?: (msg: string) => void;
}

async function defaultCapture(): Promise<Buffer> {
  const mod = await import("screenshot-desktop");
  return mod.default({ format: "png" });
}

export class ScreenMemoryRecorder {
  readonly home?: string;
  config: MemoryConfig;
  readonly store: MemoryStore;
  private capture: CaptureFn;
  private ocrFn?: OcrFn;
  private engine: OcrEngine | null = null;
  private engineTried = false;
  private probe: FrontmostFn;
  private intervalOverride?: number;
  private log: (msg: string) => void;
  private lastSig: number[] | null = null;
  private ticks = 0;
  private _captured = 0;
  private _skipped = 0;
  private _lastStatus = "idle";
  private ocrWarned = false;
  private stopping = false;
  private wake: (() => void) | null = null;
  private hotkey: { stop(): void } | null = null;

  constructor(opts: RecorderOptions = {}) {
    this.home = opts.home;
    this.config = opts.config ?? loadConfig(opts.home);
    this.intervalOverride = opts.intervalOverride;
    if (this.intervalOverride) this.config.interval_seconds = this.intervalOverride;
    this.store = opts.store ?? new MemoryStore(opts.home);
    this.capture = opts.capture ?? defaultCapture;
    this.ocrFn = opts.ocr;
    this.probe = opts.frontmost ?? detectFrontmost;
    this.log = opts.log ?? ((m) => process.stderr.write(`[memory] ${m}\n`));
  }

  get captured(): number { return this._captured; }
  get skipped(): number { return this._skipped; }
  get lastStatus(): string { return this._lastStatus; }

  /** Pick up deny-list / interval edits made by other processes. */
  reloadConfig(): void {
    const fresh = loadConfig(this.home);
    if (this.intervalOverride) fresh.interval_seconds = this.intervalOverride;
    this.config = fresh;
  }

  // -- one tick ------------------------------------------------------------

  async tick(): Promise<string> {
    this.ticks++;
    if (this.ticks % 10 === 0) this.reloadConfig();

    if (getPause(this.home) !== null) return this.done("paused");

    const { app, title } = await this.frontmost();
    if (this.config.isDenied(app, title)) return this.done(`denied:${app || title}`);

    let png: Buffer;
    try {
      png = await this.capture();
    } catch (e) {
      this.log(`capture failed: ${e}`);
      return this.done(`error:capture:${e}`);
    }

    let sig: number[] | null = null;
    try {
      sig = await frameSignature(png);
    } catch (e) {
      this.log(`signature failed: ${e}`);
    }
    if (sig && this.lastSig && signatureDelta(sig, this.lastSig) < this.config.dedupe_threshold) {
      return this.done("duplicate");
    }
    this.lastSig = sig;

    let text = "";
    try {
      text = await this.recognize(png);
    } catch (e) {
      if (!this.ocrWarned) {
        this.log(`OCR failed — frames will be stored without text: ${e}`);
        this.ocrWarned = true;
      }
      text = "";
    }

    let thumb: Buffer | null = null;
    let tw = 0;
    let th = 0;
    try {
      const t = await makeThumbnail(png, this.config.thumbnail_width, this.config.thumbnail_quality);
      thumb = t.jpeg; tw = t.width; th = t.height;
    } catch (e) {
      this.log(`thumbnail failed: ${e}`);
    }

    this.store.add({ ts: Date.now() / 1000, app, title, text, thumb, width: tw, height: th });
    this._captured++;
    if (this._captured % 20 === 1) this.housekeep();
    return this.done("captured");
  }

  /** Apply retention + storage cap. */
  housekeep(): { expired: number; capped: number } {
    const expired = this.store.enforceRetention(this.config.retention_days);
    const capped = this.store.enforceCap(this.config.storage_cap_mb * 1024 * 1024);
    if (expired || capped) this.log(`housekeeping: removed ${expired} expired, ${capped} over-cap frames`);
    return { expired, capped };
  }

  private async recognize(png: Buffer): Promise<string> {
    if (this.ocrFn) return this.ocrFn(png);
    if (!this.engine && !this.engineTried) {
      this.engineTried = true;
      this.engine = await createOcrEngine(this.config.ocr_backend as OcrPreference, this.home);
      if (!this.engine) throw new Error("no OCR backend available");
    }
    if (!this.engine) throw new Error("no OCR backend available");
    return this.engine.recognize(png);
  }

  private async frontmost(): Promise<Frontmost> {
    try {
      const f = await this.probe();
      const app = (f.app ?? "").trim();
      const title = (f.title ?? "").trim();
      return { app, title: title === app ? "" : title };
    } catch {
      return { app: "", title: "" };
    }
  }

  private done(status: string): string {
    this._lastStatus = status;
    if (status !== "captured") this._skipped++;
    writeDaemonState(this.home, {
      status, captured: this._captured, skipped: this._skipped, interval: this.config.interval_seconds,
    });
    return status;
  }

  // -- loop ----------------------------------------------------------------

  async run(): Promise<void> {
    this.stopping = false;
    writeDaemonState(this.home, { status: "starting", captured: 0, skipped: 0, interval: this.config.interval_seconds });
    await this.startHotkey();
    try {
      while (!this.stopping) {
        const started = Date.now();
        try {
          await this.tick();
        } catch (e) {
          this.log(`tick failed: ${e}`);
          this.done(`error:${e}`);
        }
        const elapsed = (Date.now() - started) / 1000;
        const delay = Math.max(1, this.config.interval_seconds - elapsed);
        if (this.stopping) break;
        await new Promise<void>((resolve) => {
          const t = setTimeout(() => { this.wake = null; resolve(); }, delay * 1000);
          this.wake = () => { clearTimeout(t); this.wake = null; resolve(); };
        });
      }
    } finally {
      this.stopHotkey();
      if (this.engine) await this.engine.close();
      clearDaemonState(this.home);
      this.store.close();
    }
  }

  stop(): void {
    this.stopping = true;
    this.wake?.();
  }

  // -- pause hotkey ----------------------------------------------------------

  /** Flip the pause flag.  Returns the new paused state. */
  togglePause(): boolean {
    if (getPause(this.home) !== null) {
      clearPause(this.home);
      console.log(`[${now()}] screen memory resumed`);
      return false;
    }
    setPause(this.home, { reason: "hotkey" });
    console.log(`[${now()}] screen memory paused — press the hotkey again to resume`);
    return true;
  }

  private async startHotkey(): Promise<void> {
    const combo = (this.config.pause_hotkey ?? "").trim();
    if (!combo) return;
    try {
      const { registerHotkey } = await import("./hotkey.js");
      this.hotkey = await registerHotkey(combo, () => this.togglePause());
      this.log(`pause hotkey armed: ${combo}`);
    } catch (e) {
      this.log(`pause hotkey unavailable (${(e as Error).message}). Use 'opendesk-js memory pause' instead.`);
      this.hotkey = null;
    }
  }

  private stopHotkey(): void {
    try { this.hotkey?.stop(); } catch { /* ignore */ }
    this.hotkey = null;
  }
}

// ---------------------------------------------------------------------------
// Daemon entry point
// ---------------------------------------------------------------------------

export async function startDaemon(opts: { home?: string; interval?: number } = {}): Promise<void> {
  const config = loadConfig(opts.home);
  const backend = availableBackend(config.ocr_backend as OcrPreference, opts.home);
  if (backend === "macos-vision") {
    console.log("Preparing the macOS Vision OCR helper (one-time compile, may take a minute)…");
    await ensureVisionHelper(opts.home);
  }
  if (!backend) {
    process.stderr.write(
      "WARNING: no OCR backend available — frames will be stored without text.\n" +
      "  tesseract.js is bundled; on macOS install Xcode command-line tools for the faster Vision engine.\n",
    );
  }

  const recorder = new ScreenMemoryRecorder({ home: opts.home, config, intervalOverride: opts.interval });
  const cfg = recorder.config;
  console.log("opendesk screen memory (js)");
  console.log(`  store:      ${recorder.store.dir}`);
  console.log(`  interval:   every ${cfg.interval_seconds}s`);
  console.log(`  cap:        ${cfg.storage_cap_mb} MB, retention ${cfg.retention_days} days`);
  console.log(`  deny list:  ${cfg.deny_apps.join(", ") || "(empty)"}`);
  console.log(`  OCR:        ${backend ?? "unavailable"}`);
  console.log(`  pause key:  ${cfg.pause_hotkey || "(disabled)"}`);
  if (getPause(opts.home) !== null) {
    console.log("  state:      PAUSED (resume with `opendesk-js memory resume` or the hotkey)");
  }
  console.log("Ctrl-C to stop.\n");

  const stop = () => recorder.stop();
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
  await recorder.run();
  console.log(`\nStopped. ${recorder.captured} frames captured this run.`);
}

function now(): string {
  return new Date().toTimeString().slice(0, 8);
}
