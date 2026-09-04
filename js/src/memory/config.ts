/**
 * Screen-memory configuration and shared on-disk state — mirrors Python's
 * opendesk/memory/config.py and uses the *same* file formats, so a Python
 * and a JS daemon/tool can share `~/.opendesk/memory`:
 *
 *   config.json  — MemoryConfig (snake_case keys, as in Python)
 *   paused.json  — present while capture is paused
 *   daemon.json  — heartbeat written by the capture loop
 *
 * The daemon and the agent's MCP server are separate processes, so all
 * shared state goes through these files.  Nothing here touches the network.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const DEFAULT_HOME = path.join(os.homedir(), ".opendesk");
export const MEMORY_DIR_NAME = "memory";

export const DEFAULT_DENY_APPS = [
  "1Password",
  "Bitwarden",
  "KeePassXC",
  "Keychain Access",
  "LastPass",
  "Dashlane",
];

export function defaultHotkey(): string {
  return process.platform === "darwin" ? "<cmd>+<shift>+<alt>+p" : "<ctrl>+<shift>+<alt>+p";
}

export function resolveHomeDir(home?: string): string {
  if (home) return path.resolve(home);
  const env = process.env["OPENDESK_HOME"];
  return env ? path.resolve(env) : DEFAULT_HOME;
}

/** Return (and create) `<home>/memory`. */
export function memoryDir(home?: string): string {
  const d = path.join(resolveHomeDir(home), MEMORY_DIR_NAME);
  fs.mkdirSync(d, { recursive: true });
  try { fs.chmodSync(d, 0o700); } catch { /* windows */ }
  return d;
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export interface MemoryConfigData {
  interval_seconds: number;
  storage_cap_mb: number;
  retention_days: number;
  deny_apps: string[];
  pause_hotkey: string;
  thumbnail_width: number;
  thumbnail_quality: number;
  dedupe_threshold: number;
  ocr_backend: string;
}

export class MemoryConfig implements MemoryConfigData {
  interval_seconds = 30;
  storage_cap_mb = 2048;
  retention_days = 30;
  deny_apps: string[] = [...DEFAULT_DENY_APPS];
  pause_hotkey = defaultHotkey();
  thumbnail_width = 480;
  thumbnail_quality = 60;
  dedupe_threshold = 0.02;
  ocr_backend = "auto";

  constructor(init: Partial<MemoryConfigData> = {}) {
    Object.assign(this, filterKnown(init));
  }

  static fromDict(data: Record<string, unknown>): MemoryConfig {
    return new MemoryConfig(filterKnown(data));
  }

  toDict(): MemoryConfigData {
    return {
      interval_seconds: this.interval_seconds,
      storage_cap_mb: this.storage_cap_mb,
      retention_days: this.retention_days,
      deny_apps: [...this.deny_apps],
      pause_hotkey: this.pause_hotkey,
      thumbnail_width: this.thumbnail_width,
      thumbnail_quality: this.thumbnail_quality,
      dedupe_threshold: this.dedupe_threshold,
      ocr_backend: this.ocr_backend,
    };
  }

  /** True when the app name or window title matches a deny-list entry. */
  isDenied(app?: string, title?: string): boolean {
    const hay = [app, title].filter(Boolean).join(" \n ").toLowerCase();
    if (!hay) return false;
    return this.deny_apps.some((p) => p.trim() && hay.includes(p.trim().toLowerCase()));
  }

  denyAdd(pattern: string): boolean {
    const p = pattern.trim();
    if (!p) return false;
    if (this.deny_apps.some((x) => x.toLowerCase() === p.toLowerCase())) return false;
    this.deny_apps.push(p);
    return true;
  }

  denyRemove(pattern: string): boolean {
    const before = this.deny_apps.length;
    const p = pattern.trim().toLowerCase();
    this.deny_apps = this.deny_apps.filter((x) => x.toLowerCase() !== p);
    return this.deny_apps.length < before;
  }
}

const KNOWN_KEYS: (keyof MemoryConfigData)[] = [
  "interval_seconds", "storage_cap_mb", "retention_days", "deny_apps", "pause_hotkey",
  "thumbnail_width", "thumbnail_quality", "dedupe_threshold", "ocr_backend",
];

function filterKnown(data: Record<string, unknown>): Partial<MemoryConfigData> {
  const out: Record<string, unknown> = {};
  for (const k of KNOWN_KEYS) {
    if (data[k] !== undefined) out[k] = data[k];
  }
  return out as Partial<MemoryConfigData>;
}

export function configPath(home?: string): string {
  return path.join(memoryDir(home), "config.json");
}

export function loadConfig(home?: string): MemoryConfig {
  const p = configPath(home);
  if (!fs.existsSync(p)) return new MemoryConfig();
  try {
    return MemoryConfig.fromDict(JSON.parse(fs.readFileSync(p, "utf8")));
  } catch {
    return new MemoryConfig();
  }
}

export function saveConfig(config: MemoryConfig, home?: string): string {
  const p = configPath(home);
  atomicWrite(p, JSON.stringify(config.toDict(), null, 2));
  return p;
}

// ---------------------------------------------------------------------------
// Pause state
// ---------------------------------------------------------------------------

export interface PauseState {
  since: number;
  until: number | null;
  reason: string;
}

export function pausePath(home?: string): string {
  return path.join(memoryDir(home), "paused.json");
}

export function describePause(state: PauseState): string {
  if (state.until === null) return "paused until resumed";
  const remaining = Math.max(0, Math.floor(state.until - Date.now() / 1000));
  return `paused for another ${formatDuration(remaining)}`;
}

/** Current pause state, clearing it if it has expired. */
export function getPause(home?: string): PauseState | null {
  const p = pausePath(home);
  if (!fs.existsSync(p)) return null;
  let state: PauseState;
  try {
    const data = JSON.parse(fs.readFileSync(p, "utf8"));
    state = {
      since: Number(data.since ?? 0),
      until: data.until == null ? null : Number(data.until),
      reason: String(data.reason ?? ""),
    };
  } catch {
    fs.rmSync(p, { force: true });
    return null;
  }
  if (state.until !== null && Date.now() / 1000 >= state.until) {
    fs.rmSync(p, { force: true });
    return null;
  }
  return state;
}

export function setPause(home?: string, opts: { durationSeconds?: number; reason?: string } = {}): PauseState {
  const now = Date.now() / 1000;
  const state: PauseState = {
    since: now,
    until: opts.durationSeconds ? now + opts.durationSeconds : null,
    reason: opts.reason ?? "",
  };
  atomicWrite(pausePath(home), JSON.stringify(state));
  return state;
}

export function clearPause(home?: string): boolean {
  const p = pausePath(home);
  if (fs.existsSync(p)) {
    fs.rmSync(p, { force: true });
    return true;
  }
  return false;
}

export function isPaused(home?: string): boolean {
  return getPause(home) !== null;
}

// ---------------------------------------------------------------------------
// Daemon heartbeat
// ---------------------------------------------------------------------------

export interface DaemonState {
  pid: number;
  heartbeat: number;
  status?: string;
  captured?: number;
  skipped?: number;
  interval?: number;
  [k: string]: unknown;
}

export function daemonStatePath(home?: string): string {
  return path.join(memoryDir(home), "daemon.json");
}

export function writeDaemonState(home: string | undefined, fields: Record<string, unknown>): void {
  const data = { pid: process.pid, heartbeat: Date.now() / 1000, ...fields };
  atomicWrite(daemonStatePath(home), JSON.stringify(data));
}

export function readDaemonState(home?: string): DaemonState | null {
  const p = daemonStatePath(home);
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, "utf8")) as DaemonState;
  } catch {
    return null;
  }
}

export function clearDaemonState(home?: string): void {
  fs.rmSync(daemonStatePath(home), { force: true });
}

/** Heuristic liveness: pid exists *and* heartbeat is recent. */
export function daemonAlive(home?: string, staleAfter = 300): boolean {
  const state = readDaemonState(home);
  if (!state) return false;
  const pid = Number(state.pid ?? 0);
  if (pid <= 0 || !pidAlive(pid)) return false;
  return Date.now() / 1000 - Number(state.heartbeat ?? 0) < staleAfter;
}

function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === "EPERM";
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function atomicWrite(p: string, text: string): void {
  const tmp = p + ".tmp";
  fs.writeFileSync(tmp, text, { mode: 0o600 });
  fs.renameSync(tmp, p);
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h${String(m).padStart(2, "0")}m`;
  }
  return `${Math.floor(seconds / 86400)}d`;
}

/** Parse "30m", "2h", "1d", "90s", "2 hours", or bare seconds. */
export function parseDuration(text: string): number {
  const t = String(text ?? "").trim().toLowerCase();
  if (!t) throw new Error("empty duration");
  const units: Record<string, number> = { s: 1, m: 60, h: 3600, d: 86400, w: 604800 };
  const m = /^(\d+(?:\.\d+)?)\s*([smhdw])$/.exec(t);
  if (m) return parseFloat(m[1]) * units[m[2]];
  if (/^\d+(?:\.\d+)?$/.test(t)) return parseFloat(t);
  for (const [word, mult] of [["minute", 60], ["hour", 3600], ["day", 86400], ["week", 604800], ["second", 1]] as const) {
    if (t.includes(word)) {
      const num = parseFloat(t.replace(/[^\d.]/g, "")) || 1;
      return num * mult;
    }
  }
  throw new Error(`Cannot parse duration: '${text}' (try '30m', '2h', '1d')`);
}
