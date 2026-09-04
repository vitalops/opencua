import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  MemoryConfig, loadConfig, saveConfig, setPause, clearPause, isPaused, getPause,
  parseDuration, writeDaemonState, daemonAlive,
} from "../src/memory/config.js";
import { parseWhen, parseRange } from "../src/memory/timeparse.js";
import { MemoryStore, queryTerms, makeSnippet } from "../src/memory/store.js";
import { makeThumbnail, frameSignature, signatureDelta } from "../src/memory/image.js";
import { ScreenMemoryRecorder } from "../src/memory/recorder.js";
import { renderLaunchdPlist, renderSystemdUnit, renderSchtasksCommand, LAUNCHD_LABEL } from "../src/memory/service.js";
import { parseCombo } from "../src/memory/hotkey.js";
import { MemoryTool } from "../src/tools/memory.js";
import { createRegistry } from "../src/registry.js";
import type { ToolContext } from "../src/tools/base.js";

let home: string;
beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "odmem-"));
});
afterEach(() => {
  fs.rmSync(home, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function png(color = 0xffffffff, size: [number, number] = [64, 48], seed = 0): Promise<Buffer> {
  const mod = (await import("jimp")) as unknown as typeof import("jimp") & { default?: typeof import("jimp") };
  const Jimp = mod.default ?? mod;
  const img = new Jimp(size[0], size[1], color);
  if (seed) {
    for (let y = 5; y < 25; y++) for (let x = seed % 40; x < seed % 40 + 20; x++) img.setPixelColor(0x000000ff, x, y);
  }
  return img.getBufferAsync(Jimp.MIME_PNG);
}

function ts(daysAgo: number, hour = 12): number {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  d.setHours(hour, 0, 0, 0);
  return d.getTime() / 1000;
}

function seeded(h: string): MemoryStore {
  const s = new MemoryStore(h);
  s.add({ ts: ts(3), app: "Terminal", title: "zsh", text: "Error: ECONNREFUSED 127.0.0.1:5432", thumb: Buffer.from("jpg1") });
  s.add({ ts: ts(2), app: "Google Chrome", title: "Grafana — Ops dashboard", text: "Ops dashboard p95 latency 120ms", thumb: Buffer.from("jpg2") });
  s.add({ ts: ts(1), app: "Preview", title: "invoice.pdf", text: "Invoice INV-2026-0042 total $1,234.00", thumb: Buffer.from("jpg3") });
  s.add({ ts: ts(0, 9), app: "Google Chrome", title: "Grafana — Ops dashboard", text: "Ops dashboard error rate 0.2%", thumb: Buffer.from("jpg4") });
  s.add({ ts: ts(0, 10), app: "Slack", title: "#general", text: "lunch at noon?", thumb: null });
  return s;
}

const ctx = (h: string): ToolContext => ({ sessionId: "mem-test", metadata: { opendeskHome: h } } as ToolContext);

// ---------------------------------------------------------------------------
// Config / pause
// ---------------------------------------------------------------------------

describe("MemoryConfig", () => {
  it("round-trips through config.json with python-compatible keys", () => {
    const cfg = loadConfig(home);
    expect(cfg.interval_seconds).toBe(30);
    expect(cfg.deny_apps).toContain("1Password");
    cfg.interval_seconds = 12;
    cfg.denyAdd("Signal");
    saveConfig(cfg, home);
    const raw = JSON.parse(fs.readFileSync(path.join(home, "memory", "config.json"), "utf8"));
    expect(raw.interval_seconds).toBe(12);
    expect(raw.storage_cap_mb).toBe(2048);
    expect(loadConfig(home).deny_apps).toContain("Signal");
  });

  it("ignores unknown keys and survives corrupt files", () => {
    fs.mkdirSync(path.join(home, "memory"), { recursive: true });
    fs.writeFileSync(path.join(home, "memory", "config.json"), '{"interval_seconds": 5, "bogus": 1}');
    expect(loadConfig(home).interval_seconds).toBe(5);
    fs.writeFileSync(path.join(home, "memory", "config.json"), "{not json");
    expect(loadConfig(home).interval_seconds).toBe(30);
  });

  it("deny matching is case-insensitive substring on app or title", () => {
    const cfg = new MemoryConfig({ deny_apps: ["1password", "bank"] });
    expect(cfg.isDenied("1Password 8")).toBe(true);
    expect(cfg.isDenied("Safari", "My Bank — Accounts")).toBe(true);
    expect(cfg.isDenied("Safari", "News")).toBe(false);
    expect(cfg.isDenied("", "")).toBe(false);
  });

  it("denyAdd / denyRemove", () => {
    const cfg = new MemoryConfig({ deny_apps: [] });
    expect(cfg.denyAdd("Signal")).toBe(true);
    expect(cfg.denyAdd("signal")).toBe(false);
    expect(cfg.denyRemove("SIGNAL")).toBe(true);
    expect(cfg.denyRemove("Signal")).toBe(false);
    expect(cfg.denyAdd("  ")).toBe(false);
  });

  it("pause until resumed and pause with expiry", async () => {
    expect(isPaused(home)).toBe(false);
    expect(setPause(home).until).toBeNull();
    expect(isPaused(home)).toBe(true);
    expect(clearPause(home)).toBe(true);
    expect(clearPause(home)).toBe(false);
    setPause(home, { durationSeconds: 0.05 });
    expect(isPaused(home)).toBe(true);
    await new Promise((r) => setTimeout(r, 100));
    expect(getPause(home)).toBeNull();
    expect(fs.existsSync(path.join(home, "memory", "paused.json"))).toBe(false);
  });

  it("parseDuration", () => {
    expect(parseDuration("30m")).toBe(1800);
    expect(parseDuration("2h")).toBe(7200);
    expect(parseDuration("1d")).toBe(86400);
    expect(parseDuration("90")).toBe(90);
    expect(parseDuration("2 hours")).toBe(7200);
    expect(() => parseDuration("soon")).toThrow();
  });

  it("daemonAlive needs a live pid and a fresh heartbeat", () => {
    expect(daemonAlive(home)).toBe(false);
    writeDaemonState(home, { status: "captured" });
    expect(daemonAlive(home)).toBe(true);
    expect(daemonAlive(home, 0)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Time parsing
// ---------------------------------------------------------------------------

describe("parseWhen", () => {
  const NOW = new Date(2026, 8, 4, 15, 30); // Friday 2026-09-04
  const local = (...a: number[]) => new Date(a[0], a[1] - 1, a[2], a[3] ?? 0, a[4] ?? 0).getTime() / 1000;

  it("relative durations are open-ended", () => {
    const [s, e] = parseWhen("2h", NOW);
    expect(e).toBeNull();
    expect(s).toBeCloseTo(NOW.getTime() / 1000 - 7200, 3);
  });
  it("yesterday is a whole day", () => {
    expect(parseWhen("yesterday", NOW)).toEqual([local(2026, 9, 3), local(2026, 9, 4)]);
  });
  it("weekday → most recent; last <weekday>", () => {
    expect(parseWhen("tuesday", NOW)).toEqual([local(2026, 9, 1), local(2026, 9, 2)]);
    expect(parseWhen("friday", NOW)[0]).toBe(local(2026, 9, 4));
    expect(parseWhen("last friday", NOW)[0]).toBe(local(2026, 8, 28));
  });
  it("day parts", () => {
    expect(parseWhen("tuesday afternoon", NOW)).toEqual([local(2026, 9, 1, 12), local(2026, 9, 1, 17)]);
    expect(parseWhen("yesterday morning", NOW)).toEqual([local(2026, 9, 3, 5), local(2026, 9, 3, 12)]);
  });
  it("calendar periods", () => {
    expect(parseWhen("last week", NOW)).toEqual([local(2026, 8, 24), local(2026, 8, 31)]);
    expect(parseWhen("this month", NOW)).toEqual([local(2026, 9, 1), null]);
    expect(parseWhen("last month", NOW)).toEqual([local(2026, 8, 1), local(2026, 9, 1)]);
  });
  it("ISO date and datetime", () => {
    expect(parseWhen("2026-09-01", NOW)).toEqual([local(2026, 9, 1), local(2026, 9, 2)]);
    expect(parseWhen("2026-09-01 14:30", NOW)).toEqual([local(2026, 9, 1, 14, 30), null]);
  });
  it("N days ago", () => {
    expect(parseWhen("3 days ago", NOW)[0]).toBe(local(2026, 9, 1));
  });
  it("garbage throws", () => {
    expect(() => parseWhen("whenever", NOW)).toThrow(/Cannot parse/);
  });
  it("parseRange combines and validates", () => {
    expect(parseRange("tuesday", "thursday", NOW)).toEqual([local(2026, 9, 1), local(2026, 9, 4)]);
    expect(parseRange(undefined, undefined, NOW)).toEqual([0, null]);
    expect(() => parseRange("today", "yesterday", NOW)).toThrow();
  });
});

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

describe("MemoryStore", () => {
  it("add writes a thumbnail and a jsonl record", () => {
    const s = new MemoryStore(home);
    const f = s.add({ ts: Date.now() / 1000, app: "A", title: "t", text: "hello world", thumb: Buffer.from("jpeg") });
    expect(f.id).toBe(1);
    expect(fs.readFileSync(path.join(s.dir, f.thumb_path!)).toString()).toBe("jpeg");
    expect(s.get(1)?.text).toBe("hello world");
    expect(s.thumbnail(f)?.toString()).toBe("jpeg");
    expect(s.stats().frames).toBe(1);
    expect(s.add({ ts: Date.now() / 1000 }).id).toBe(2);
  });

  it("search finds terms with snippets, matches title/app, falls back to OR", () => {
    const s = seeded(home);
    let hits = s.search("ECONNREFUSED");
    expect(hits.map((h) => h.app)).toEqual(["Terminal"]);
    expect(hits[0].snippet).toContain("[ECONNREFUSED]");
    expect(s.search("invoice total")).toHaveLength(1);
    expect(s.search("Grafana")).toHaveLength(2);
    expect(s.search("slack")).toHaveLength(1);
    hits = s.search("invoice latency");
    expect(new Set(hits.map((h) => h.app))).toEqual(new Set(["Preview", "Google Chrome"]));
    expect(s.search("INV-2026*")).toHaveLength(1);
    expect(s.search('"$1,234.00"')).toHaveLength(1);
    expect(s.search("(((").length).toBe(s.timeline().length);
  });

  it("time and app filters", () => {
    const s = seeded(home);
    const [start, end] = parseWhen("today");
    expect(s.search("dashboard", { start, end })).toHaveLength(1);
    expect(s.search("dashboard", { app: "chrome" })).toHaveLength(2);
    expect(s.search("dashboard", { app: "Terminal" })).toHaveLength(0);
  });

  it("timeline and apps", () => {
    const s = seeded(home);
    expect(s.timeline({ limit: 2 }).map((f) => f.app)).toEqual(["Slack", "Google Chrome"]);
    expect(s.apps()[0]).toEqual(["Google Chrome", 2]);
    expect(s.timeline({ start: parseWhen("today")[0] })).toHaveLength(2);
    expect(s.timeline({ limit: 10, newestFirst: false })[0].app).toBe("Terminal");
  });

  it("delete removes thumbnails and records", () => {
    const s = seeded(home);
    const f = s.get(1)!;
    const p = path.join(s.dir, f.thumb_path!);
    expect(fs.existsSync(p)).toBe(true);
    expect(s.delete([1])).toBe(1);
    expect(fs.existsSync(p)).toBe(false);
    expect(s.get(1)).toBeNull();
    expect(s.search("ECONNREFUSED")).toHaveLength(0);
    const [start, end] = parseWhen("today");
    expect(s.deleteRange(start, end)).toBe(2);
    expect(s.deleteApp("chrome")).toBe(1);
    expect(s.stats().frames).toBe(1);
  });

  it("retention", () => {
    const s = new MemoryStore(home);
    const now = Date.now() / 1000;
    for (const d of [10, 5, 2.5, 1, 0]) s.add({ ts: now - d * 86400, app: "A", text: `${d}d`, thumb: Buffer.from("x") });
    expect(s.enforceRetention(0)).toBe(0);
    expect(s.enforceRetention(2)).toBe(3);
    expect(new Set(s.timeline().map((f) => f.text))).toEqual(new Set(["1d", "0d"]));
  });

  it("storage cap rolls oldest first and keeps the newest", () => {
    const s = new MemoryStore(home);
    const base = Date.now() / 1000 - 3600;
    for (let i = 0; i < 20; i++) s.add({ ts: base + i, app: "A", text: "x".repeat(10), thumb: Buffer.alloc(100_000, 1) });
    expect(s.stats().totalBytes).toBeGreaterThan(2_000_000);
    const deleted = s.enforceCap(1_000_000);
    expect(deleted).toBeGreaterThan(0);
    expect(deleted).toBeLessThan(20);
    const rem = s.timeline({ limit: 100, newestFirst: false });
    expect(rem[rem.length - 1].ts).toBe(base + 19);
    expect(rem[0].ts).toBeGreaterThan(base);
    expect(s.stats().totalBytes).toBeLessThanOrEqual(1_000_000);
    expect(s.enforceCap(1e12)).toBe(0);
  });

  it("cap never deletes the last frame", () => {
    const s = new MemoryStore(home);
    for (let i = 0; i < 3; i++) s.add({ ts: 1000 + i, app: "A", thumb: Buffer.alloc(100_000, 1) });
    expect(s.enforceCap(1)).toBe(2);
    expect(s.stats().frames).toBe(1);
    expect(s.get(3)).not.toBeNull();
  });

  it("clear and helpers", () => {
    const s = seeded(home);
    expect(s.clear()).toBe(5);
    expect(s.stats().frames).toBe(0);
    expect(fs.readdirSync(s.thumbsDir)).toHaveLength(0);
    expect(queryTerms('error "connection refused" port:5432')).toEqual(["error", "connection refused", "port:5432"]);
    expect(queryTerms("...")).toEqual([]);
    expect(makeSnippet("a b error c d", ["error"])).toContain("[error]");
  });
});

// ---------------------------------------------------------------------------
// Image helpers
// ---------------------------------------------------------------------------

describe("image helpers", () => {
  it("thumbnail downscales to JPEG", async () => {
    const t = await makeThumbnail(await png(0xffffffff, [1600, 900]), 400);
    expect([t.width, t.height]).toEqual([400, 225]);
    expect(t.jpeg.subarray(0, 3)).toEqual(Buffer.from([0xff, 0xd8, 0xff]));
  });
  it("signature delta separates similar from different frames", async () => {
    const a = await frameSignature(await png(0xffffffff));
    const b = await frameSignature(await png(0xfafafaff));
    const c = await frameSignature(await png(0x000000ff));
    expect(signatureDelta(a, b)).toBeLessThan(0.05);
    expect(signatureDelta(a, c)).toBeGreaterThan(0.9);
    expect(signatureDelta(null, a)).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Recorder
// ---------------------------------------------------------------------------

function recorder(h: string, frames: Array<{ png: Buffer; app: string; title: string }>, extra: Partial<ConstructorParameters<typeof ScreenMemoryRecorder>[0]> = {}) {
  const queue = [...frames];
  const cur = () => queue[0];
  return new ScreenMemoryRecorder({
    home: h,
    config: extra.config ?? new MemoryConfig({ deny_apps: ["1Password"] }),
    capture: async () => { const f = queue.length > 1 ? queue.shift()! : queue[0]; return f.png; },
    frontmost: async () => ({ app: cur().app, title: cur().title }),
    ocr: extra.ocr ?? (async (b) => `text for ${b.length} bytes`),
    log: () => {},
    ...extra,
  });
}

describe("ScreenMemoryRecorder", () => {
  it("captures, OCRs, thumbnails, and indexes", async () => {
    const rec = recorder(home, [{ png: await png(0xffffffff, [64, 48], 1), app: "Terminal", title: "zsh" }], { ocr: async () => "npm ERR! code ELIFECYCLE" });
    expect(await rec.tick()).toBe("captured");
    const s = new MemoryStore(home);
    const hits = s.search("ELIFECYCLE");
    expect(hits).toHaveLength(1);
    expect(hits[0].app).toBe("Terminal");
    expect(hits[0].title).toBe("zsh");
    expect(s.thumbnail(hits[0])!.subarray(0, 3)).toEqual(Buffer.from([0xff, 0xd8, 0xff]));
    expect(hits[0].width).toBe(64);
  });

  it("skips duplicate frames", async () => {
    const same = await png(0xffffffff, [64, 48], 3);
    const rec = recorder(home, [
      { png: same, app: "A", title: "" }, { png: same, app: "A", title: "" }, { png: await png(0x000000ff), app: "A", title: "" },
    ]);
    expect(await rec.tick()).toBe("captured");
    expect(await rec.tick()).toBe("duplicate");
    expect(await rec.tick()).toBe("captured");
    expect(rec.captured).toBe(2);
    expect(rec.skipped).toBe(1);
  });

  it("denied apps and titles are never captured", async () => {
    let captures = 0;
    const rec = recorder(home, [{ png: await png(), app: "1Password 8", title: "Vault" }], {
      capture: async () => { captures++; return png(); },
    });
    expect(await rec.tick()).toMatch(/^denied:/);
    expect(captures).toBe(0);
    const rec2 = recorder(home, [{ png: await png(), app: "Safari", title: "Acme Bank — Login" }], { config: new MemoryConfig({ deny_apps: ["bank"] }) });
    expect(await rec2.tick()).toMatch(/^denied:/);
  });

  it("pause flag skips capture; hotkey toggles it", async () => {
    const rec = recorder(home, [{ png: await png(), app: "A", title: "" }]);
    setPause(home);
    expect(await rec.tick()).toBe("paused");
    clearPause(home);
    expect(await rec.tick()).toBe("captured");
    expect(rec.togglePause()).toBe(true);
    expect(isPaused(home)).toBe(true);
    expect(rec.togglePause()).toBe(false);
    expect(isPaused(home)).toBe(false);
  });

  it("OCR failure stores the frame without text; capture errors are reported", async () => {
    const rec = recorder(home, [{ png: await png(), app: "A", title: "" }], { ocr: async () => { throw new Error("no engine"); } });
    expect(await rec.tick()).toBe("captured");
    expect(new MemoryStore(home).get(1)!.text).toBe("");
    const rec2 = recorder(home, [{ png: await png(), app: "A", title: "" }], { capture: async () => { throw new Error("no permission"); } });
    expect(await rec2.tick()).toMatch(/^error:capture:/);
  });

  it("reloads config so deny-list edits from another process apply", async () => {
    const cfg = new MemoryConfig({ deny_apps: [] });
    saveConfig(cfg, home);
    const frames = [];
    for (let i = 1; i < 30; i++) frames.push({ png: await png(0xffffffff, [64, 48], i), app: "Slack", title: "" });
    const rec = recorder(home, frames, { config: cfg });
    expect(await rec.tick()).toBe("captured");
    const cfg2 = loadConfig(home);
    cfg2.denyAdd("Slack");
    saveConfig(cfg2, home);
    let last = "";
    for (let i = 0; i < 10; i++) last = await rec.tick();
    expect(last).toMatch(/^denied:/);
  });

  it("interval override survives reload; housekeeping applies the cap", async () => {
    const cfg = new MemoryConfig({ storage_cap_mb: 1, retention_days: 365 });
    saveConfig(cfg, home);
    const rec = recorder(home, [{ png: await png(), app: "A", title: "" }], { intervalOverride: 7, config: cfg });
    expect(rec.config.interval_seconds).toBe(7);
    rec.reloadConfig();
    expect(rec.config.interval_seconds).toBe(7);
    expect(rec.config.storage_cap_mb).toBe(1);
    const now = Date.now() / 1000;
    for (let i = 0; i < 30; i++) rec.store.add({ ts: now - 30 + i, app: "A", thumb: Buffer.alloc(100_000, 1) });
    const r = rec.housekeep();
    expect(r.expired).toBe(0);
    expect(r.capped).toBeGreaterThan(0);
    expect(rec.store.stats().totalBytes).toBeLessThanOrEqual(1024 * 1024);
  });

  it("run loop stops cleanly and clears the heartbeat", async () => {
    const rec = recorder(home, [{ png: await png(0xffffffff, [64, 48], 1), app: "A", title: "" }, { png: await png(0xffffffff, [64, 48], 9), app: "A", title: "" }], {
      config: new MemoryConfig({ interval_seconds: 5, pause_hotkey: "" }),
    });
    const done = rec.run();
    await new Promise((r) => setTimeout(r, 400));
    expect(daemonAlive(home)).toBe(true);
    rec.stop();
    await done;
    expect(daemonAlive(home)).toBe(false);
    expect(rec.captured).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tool
// ---------------------------------------------------------------------------

describe("MemoryTool", () => {
  it("is registered with the expected schema", () => {
    const reg = createRegistry();
    const t = reg.get("memory");
    expect((t.schema as { properties: { action: { enum: string[] } } }).properties.action.enum).toContain("search");
  });

  it("search returns hits with ids and honours time phrases", async () => {
    seeded(home);
    const tool = new MemoryTool();
    let r = await tool.execute(ctx(home), { action: "search", query: "error", app: "Terminal" });
    expect(r.error).toBe(false);
    expect(r.output).toContain("ECONNREFUSED");
    expect(r.metadata["count"]).toBe(1);
    r = await tool.execute(ctx(home), { action: "search", query: "dashboard", since: "today" });
    expect(r.metadata["count"]).toBe(1);
    r = await tool.execute(ctx(home), { action: "search", query: "dashboard", since: "this week" });
    expect(r.metadata["count"]).toBeGreaterThanOrEqual(1);
  });

  it("validates input and hints when empty", async () => {
    const tool = new MemoryTool();
    let r = await tool.execute(ctx(home), { action: "search" });
    expect(r.error).toBe(true);
    r = await tool.execute(ctx(home), { action: "search", query: "x", since: "whenever" });
    expect(r.error).toBe(true);
    expect(r.output).toMatch(/Cannot parse/);
    r = await tool.execute(ctx(home), { action: "search", query: "anything" });
    expect(r.output).toContain("opendesk-js memory start");
  });

  it("show returns text and thumbnail attachment", async () => {
    seeded(home);
    const tool = new MemoryTool();
    let r = await tool.execute(ctx(home), { action: "show", id: 3 });
    expect(r.output).toContain("INV-2026-0042");
    expect(r.attachments[0].mediaType).toBe("image/jpeg");
    expect(r.attachments[0].content.toString()).toBe("jpg3");
    r = await tool.execute(ctx(home), { action: "show", id: 3, includeImage: false });
    expect(r.attachments).toHaveLength(0);
    r = await tool.execute(ctx(home), { action: "show", id: 999 });
    expect(r.error).toBe(true);
  });

  it("timeline, status, pause/resume, deny, config", async () => {
    seeded(home);
    const tool = new MemoryTool();
    let r = await tool.execute(ctx(home), { action: "timeline", since: "this week", app: "chrome" });
    expect(r.metadata["count"]).toBe(2);
    expect(r.output).toContain("Grafana");
    r = await tool.execute(ctx(home), { action: "status" });
    expect(r.output).toContain("frames:    5");
    expect(r.output).toContain("NOT running");
    r = await tool.execute(ctx(home), { action: "pause", duration: "1h" });
    expect(isPaused(home)).toBe(true);
    r = await tool.execute(ctx(home), { action: "status" });
    expect(r.output).toContain("PAUSED");
    await tool.execute(ctx(home), { action: "resume" });
    expect(isPaused(home)).toBe(false);
    r = await tool.execute(ctx(home), { action: "deny", denyAdd: "Signal" });
    expect(loadConfig(home).deny_apps).toContain("Signal");
    await tool.execute(ctx(home), { action: "deny", denyRemove: "Signal" });
    expect(loadConfig(home).deny_apps).not.toContain("Signal");
    r = await tool.execute(ctx(home), { action: "config", intervalSeconds: 15, storageCapMb: 512, retentionDays: 7 });
    const cfg = loadConfig(home);
    expect([cfg.interval_seconds, cfg.storage_cap_mb, cfg.retention_days]).toEqual([15, 512, 7]);
    expect(r.output).toContain("Updated");
  });

  it("delete requires scope and confirm", async () => {
    seeded(home);
    const tool = new MemoryTool();
    let r = await tool.execute(ctx(home), { action: "delete" });
    expect(r.error).toBe(true);
    r = await tool.execute(ctx(home), { action: "delete", app: "chrome" });
    expect(r.output).toContain("would be deleted");
    expect(new MemoryStore(home).stats().frames).toBe(5);
    r = await tool.execute(ctx(home), { action: "delete", app: "chrome", confirm: true });
    expect(r.metadata["count"]).toBe(2);
    expect(new MemoryStore(home).stats().frames).toBe(3);
  });

  it("consults the permission handler and accepts home via constructor", async () => {
    const { PermissionDeniedError } = await import("../src/tools/base.js");
    const denyCtx: ToolContext = { sessionId: "x", permissionHandler: async () => { throw new PermissionDeniedError("nope"); } };
    await expect(new MemoryTool({ home }).execute(denyCtx, { action: "status" })).rejects.toBeInstanceOf(PermissionDeniedError);
    seeded(home);
    const r = await new MemoryTool({ home }).execute({ sessionId: "y" }, { action: "status" });
    expect(r.output).toContain("frames:    5");
  });
});

// ---------------------------------------------------------------------------
// Service renderers + hotkey combo parsing
// ---------------------------------------------------------------------------

describe("service renderers", () => {
  it("launchd plist is well-formed and carries the args", () => {
    const plist = renderLaunchdPlist("/usr/local/bin/node", "/x/bin/opendesk.js", 20, "/tmp/h");
    expect(plist).toContain(`<string>${LAUNCHD_LABEL}</string>`);
    for (const s of ["memory", "start", "--interval", "20", "--home", "/tmp/h"]) expect(plist).toContain(`<string>${s}</string>`);
    expect(plist).toContain("/tmp/h/memory/daemon.log");
  });
  it("systemd unit", () => {
    const unit = renderSystemdUnit("/usr/bin/node", "/x/bin/opendesk.js");
    expect(unit).toContain("ExecStart=/usr/bin/node /x/bin/opendesk.js memory start\n");
    expect(unit).not.toContain("--home");
  });
  it("schtasks command", () => {
    const cmd = renderSchtasksCommand("C:\\node\\node.exe", "C:\\x\\opendesk.js", 45, "C:\\h");
    expect(cmd.startsWith('"C:\\node\\node.exe" "C:\\x\\opendesk.js" memory start')).toBe(true);
    expect(cmd).toContain("--interval 45 --home C:\\h");
  });
  it("parseCombo understands pynput syntax", () => {
    expect(parseCombo("<cmd>+<shift>+<alt>+p")).toEqual({ ctrl: false, shift: true, alt: true, meta: true, key: "p" });
    expect(parseCombo("<ctrl>+<alt>+m")).toEqual({ ctrl: true, shift: false, alt: true, meta: false, key: "m" });
    expect(() => parseCombo("<ctrl>+<alt>")).toThrow();
  });
});
