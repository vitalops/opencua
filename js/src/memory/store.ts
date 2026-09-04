/**
 * Local, searchable index of screen captures — the JS counterpart of
 * Python's opendesk/memory/store.py.
 *
 * Storage layout under `<home>/memory`:
 *
 *   frames/YYYY-MM-DD.jsonl   one JSON record per kept frame, per day
 *   frames/seq                monotonically increasing frame id
 *   thumbs/YYYY-MM-DD/<id>.jpg
 *
 * Why JSONL instead of SQLite: it needs no native module and works on every
 * Node.js version this SDK supports.  Queries are almost always bounded by a
 * time window ("tuesday", "last week"), so only the matching day files are
 * scanned; a day is a few thousand records at most.  Nothing leaves the
 * machine.
 */

import fs from "node:fs";
import path from "node:path";
import { memoryDir } from "./config.js";
import { fmtDay, fmtTs } from "./timeparse.js";

export interface Frame {
  id: number;
  ts: number;
  app: string;
  title: string;
  text: string;
  thumb_path: string | null;
  thumb_bytes: number;
  width: number;
  height: number;
  /** Populated by search(). */
  snippet?: string;
  score?: number;
}

export interface StoreStats {
  frames: number;
  oldest: number | null;
  newest: number | null;
  thumbBytes: number;
  indexBytes: number;
  totalBytes: number;
  apps: Array<[string, number]>;
}

export interface QueryOpts {
  start?: number;
  end?: number | null;
  app?: string | null;
  limit?: number;
}

export function frameWhen(f: Frame): string {
  return fmtTs(f.ts);
}

interface DayCache {
  mtimeMs: number;
  size: number;
  records: Frame[];
}

export class MemoryStore {
  readonly dir: string;
  readonly framesDir: string;
  readonly thumbsDir: string;
  private cache = new Map<string, DayCache>();

  constructor(home?: string) {
    this.dir = memoryDir(home);
    this.framesDir = path.join(this.dir, "frames");
    this.thumbsDir = path.join(this.dir, "thumbs");
    fs.mkdirSync(this.framesDir, { recursive: true });
    fs.mkdirSync(this.thumbsDir, { recursive: true });
  }

  close(): void {
    this.cache.clear();
  }

  // ------------------------------------------------------------------
  // Writes
  // ------------------------------------------------------------------

  add(input: {
    ts: number; app?: string; title?: string; text?: string;
    thumb?: Buffer | null; width?: number; height?: number;
  }): Frame {
    const id = this.nextId();
    const day = fmtDay(input.ts);
    let thumbPath: string | null = null;
    let thumbBytes = 0;
    if (input.thumb && input.thumb.length) {
      const dayDir = path.join(this.thumbsDir, day);
      fs.mkdirSync(dayDir, { recursive: true });
      const p = path.join(dayDir, `${id}.jpg`);
      fs.writeFileSync(p, input.thumb, { mode: 0o600 });
      thumbPath = path.relative(this.dir, p).split(path.sep).join("/");
      thumbBytes = input.thumb.length;
    }
    const frame: Frame = {
      id,
      ts: input.ts,
      app: input.app ?? "",
      title: input.title ?? "",
      text: input.text ?? "",
      thumb_path: thumbPath,
      thumb_bytes: thumbBytes,
      width: input.width ?? 0,
      height: input.height ?? 0,
    };
    const file = this.dayFile(day);
    fs.appendFileSync(file, JSON.stringify(frame) + "\n", { mode: 0o600 });
    this.cache.delete(day);
    return frame;
  }

  /** Delete frames (and their thumbnails) by id.  Returns count. */
  delete(ids: number[]): number {
    if (!ids.length) return 0;
    const want = new Set(ids);
    let n = 0;
    for (const day of this.days()) {
      const recs = this.loadDay(day);
      if (!recs.some((r) => want.has(r.id))) continue;
      const keep: Frame[] = [];
      for (const r of recs) {
        if (want.has(r.id)) {
          n++;
          if (r.thumb_path) fs.rmSync(path.join(this.dir, r.thumb_path), { force: true });
        } else {
          keep.push(r);
        }
      }
      this.writeDay(day, keep);
    }
    this.pruneEmptyThumbDirs();
    return n;
  }

  deleteRange(start = 0, end: number | null = null): number {
    return this.delete(this.timeline({ start, end, limit: Infinity }).map((f) => f.id));
  }

  deleteApp(appPattern: string): number {
    return this.delete(this.timeline({ app: appPattern, limit: Infinity }).map((f) => f.id));
  }

  clear(): number {
    return this.delete(this.timeline({ limit: Infinity }).map((f) => f.id));
  }

  /** Delete frames older than `retentionDays`.  Returns count. */
  enforceRetention(retentionDays: number): number {
    if (retentionDays <= 0) return 0;
    const cutoff = Date.now() / 1000 - retentionDays * 86400;
    return this.deleteRange(0, cutoff);
  }

  /**
   * Rolling deletion: drop the oldest frames until on-disk usage is under
   * `capBytes * targetFraction`.  At least one frame is always kept.
   */
  enforceCap(capBytes: number, targetFraction = 0.9): number {
    if (capBytes <= 0) return 0;
    let usage = this.stats().totalBytes;
    if (usage <= capBytes) return 0;
    const target = Math.floor(capBytes * targetFraction);
    let deleted = 0;
    while (usage > target) {
      const all = this.timeline({ limit: Infinity, newestFirst: false });
      if (all.length <= 1) break;
      const batch = all.slice(0, Math.max(1, Math.min(200, all.length - 1)));
      const freed = batch.reduce((s, f) => s + f.thumb_bytes + JSON.stringify(f).length + 1, 0);
      deleted += this.delete(batch.map((f) => f.id));
      usage -= freed;
    }
    return deleted;
  }

  // ------------------------------------------------------------------
  // Reads
  // ------------------------------------------------------------------

  get(id: number): Frame | null {
    for (const day of this.days().reverse()) {
      const hit = this.loadDay(day).find((r) => r.id === id);
      if (hit) return { ...hit };
    }
    return null;
  }

  thumbnail(frame: Frame): Buffer | null {
    if (!frame.thumb_path) return null;
    try {
      return fs.readFileSync(path.join(this.dir, frame.thumb_path));
    } catch {
      return null;
    }
  }

  /**
   * Full-text search.  All terms must match (AND); when that yields
   * nothing, retries as OR so near-misses still surface.
   */
  search(query: string, opts: QueryOpts = {}): Frame[] {
    const terms = queryTerms(query);
    if (!terms.length) return this.timeline(opts);
    let hits = this.searchTerms(terms, "AND", opts);
    if (!hits.length && terms.length > 1) hits = this.searchTerms(terms, "OR", opts);
    return hits;
  }

  timeline(opts: QueryOpts & { newestFirst?: boolean } = {}): Frame[] {
    const { start = 0, end = null, app = null, limit = 50, newestFirst = true } = opts;
    const out: Frame[] = [];
    const days = this.daysInRange(start, end);
    if (newestFirst) days.reverse();
    for (const day of days) {
      const recs = this.loadDay(day).filter((r) => this.matches(r, start, end, app));
      if (newestFirst) recs.sort((a, b) => b.ts - a.ts);
      else recs.sort((a, b) => a.ts - b.ts);
      for (const r of recs) {
        out.push({ ...r });
        if (out.length >= limit) return out;
      }
    }
    return out;
  }

  apps(opts: QueryOpts = {}): Array<[string, number]> {
    const counts = new Map<string, number>();
    for (const f of this.timeline({ ...opts, limit: Infinity })) {
      counts.set(f.app, (counts.get(f.app) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }

  stats(): StoreStats {
    let frames = 0;
    let oldest: number | null = null;
    let newest: number | null = null;
    let thumbBytes = 0;
    let indexBytes = 0;
    const counts = new Map<string, number>();
    for (const day of this.days()) {
      try { indexBytes += fs.statSync(this.dayFile(day)).size; } catch { /* gone */ }
      for (const r of this.loadDay(day)) {
        frames++;
        thumbBytes += r.thumb_bytes;
        if (oldest === null || r.ts < oldest) oldest = r.ts;
        if (newest === null || r.ts > newest) newest = r.ts;
        counts.set(r.app, (counts.get(r.app) ?? 0) + 1);
      }
    }
    const apps = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
    return { frames, oldest, newest, thumbBytes, indexBytes, totalBytes: thumbBytes + indexBytes, apps };
  }

  // ------------------------------------------------------------------
  // Internals
  // ------------------------------------------------------------------

  private searchTerms(terms: string[], joiner: "AND" | "OR", opts: QueryOpts): Frame[] {
    const { start = 0, end = null, app = null, limit = 20 } = opts;
    const scored: Frame[] = [];
    for (const day of this.daysInRange(start, end)) {
      for (const r of this.loadDay(day)) {
        if (!this.matches(r, start, end, app)) continue;
        const text = r.text.toLowerCase();
        const meta = `${r.app}\n${r.title}`.toLowerCase();
        let matched = 0;
        let score = 0;
        for (const t of terms) {
          const needle = t.toLowerCase().replace(/\*$/, "");
          const inText = countOccurrences(text, needle);
          const inMeta = meta.includes(needle);
          if (inText || inMeta) {
            matched++;
            score += Math.min(inText, 5) + (inMeta ? 3 : 0);
          }
        }
        if (matched === 0) continue;
        if (joiner === "AND" && matched < terms.length) continue;
        scored.push({ ...r, score: score + matched * 10, snippet: makeSnippet(r.text, terms) });
      }
    }
    scored.sort((a, b) => (b.score! - a.score!) || (b.ts - a.ts));
    return scored.slice(0, limit);
  }

  private matches(r: Frame, start: number, end: number | null, app: string | null): boolean {
    if (start > 0 && r.ts < start) return false;
    if (end !== null && r.ts >= end) return false;
    if (app) {
      const a = app.toLowerCase();
      if (!r.app.toLowerCase().includes(a) && !r.title.toLowerCase().includes(a)) return false;
    }
    return true;
  }

  private dayFile(day: string): string {
    return path.join(this.framesDir, `${day}.jsonl`);
  }

  /** All day keys on disk, ascending. */
  days(): string[] {
    let names: string[];
    try {
      names = fs.readdirSync(this.framesDir);
    } catch {
      return [];
    }
    return names
      .filter((n) => /^\d{4}-\d{2}-\d{2}\.jsonl$/.test(n))
      .map((n) => n.slice(0, 10))
      .sort();
  }

  private daysInRange(start: number, end: number | null): string[] {
    const lo = start > 0 ? fmtDay(start) : "";
    const hi = end !== null ? fmtDay(end) : "￿";
    return this.days().filter((d) => d >= lo && d <= hi);
  }

  private loadDay(day: string): Frame[] {
    const file = this.dayFile(day);
    let st: fs.Stats;
    try {
      st = fs.statSync(file);
    } catch {
      this.cache.delete(day);
      return [];
    }
    const cached = this.cache.get(day);
    if (cached && cached.mtimeMs === st.mtimeMs && cached.size === st.size) return cached.records;
    const records: Frame[] = [];
    for (const line of fs.readFileSync(file, "utf8").split("\n")) {
      if (!line.trim()) continue;
      try {
        const r = JSON.parse(line) as Frame;
        records.push({
          id: Number(r.id), ts: Number(r.ts), app: r.app ?? "", title: r.title ?? "",
          text: r.text ?? "", thumb_path: r.thumb_path ?? null,
          thumb_bytes: Number(r.thumb_bytes ?? 0), width: Number(r.width ?? 0), height: Number(r.height ?? 0),
        });
      } catch {
        /* skip corrupt line */
      }
    }
    this.cache.set(day, { mtimeMs: st.mtimeMs, size: st.size, records });
    return records;
  }

  private writeDay(day: string, records: Frame[]): void {
    const file = this.dayFile(day);
    this.cache.delete(day);
    if (!records.length) {
      fs.rmSync(file, { force: true });
      return;
    }
    const tmp = file + ".tmp";
    fs.writeFileSync(tmp, records.map((r) => JSON.stringify(stripSearchFields(r))).join("\n") + "\n", { mode: 0o600 });
    fs.renameSync(tmp, file);
  }

  private nextId(): number {
    const seqFile = path.join(this.framesDir, "seq");
    let n = 0;
    try { n = parseInt(fs.readFileSync(seqFile, "utf8"), 10) || 0; } catch { /* first */ }
    n += 1;
    fs.writeFileSync(seqFile, String(n), { mode: 0o600 });
    return n;
  }

  private pruneEmptyThumbDirs(): void {
    let names: string[] = [];
    try { names = fs.readdirSync(this.thumbsDir); } catch { return; }
    for (const n of names) {
      const p = path.join(this.thumbsDir, n);
      try {
        if (fs.statSync(p).isDirectory() && fs.readdirSync(p).length === 0) fs.rmdirSync(p);
      } catch { /* ignore */ }
    }
  }
}

// ---------------------------------------------------------------------------
// Query helpers
// ---------------------------------------------------------------------------

function stripSearchFields(r: Frame): Frame {
  const { snippet: _s, score: _c, ...rest } = r;
  void _s; void _c;
  return rest;
}

/** Split a query into terms.  Quoted phrases stay together. */
export function queryTerms(query: string): string[] {
  const out: string[] = [];
  const re = /"([^"]+)"|(\S+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(query ?? "")) !== null) {
    if (m[1]) {
      const phrase = m[1].trim();
      if (phrase) out.push(phrase);
      continue;
    }
    const cleaned = m[2].replace(/^["'.,;:!?()[\]{}]+|["'.,;:!?()[\]{}]+$/g, "");
    if (cleaned && /[\p{L}\p{N}]/u.test(cleaned)) out.push(cleaned);
  }
  return out;
}

function countOccurrences(hay: string, needle: string): number {
  if (!needle) return 0;
  let n = 0;
  let i = 0;
  while ((i = hay.indexOf(needle, i)) !== -1) {
    n++;
    i += needle.length;
  }
  return n;
}

/** Highlighted window of text around the first matched term. */
export function makeSnippet(text: string, terms: string[], width = 100): string {
  const low = text.toLowerCase();
  for (const t of terms) {
    const needle = t.toLowerCase().replace(/\*$/, "");
    const i = low.indexOf(needle);
    if (i >= 0) {
      const a = Math.max(0, i - Math.floor(width / 2));
      const b = Math.min(text.length, i + needle.length + Math.floor(width / 2));
      const before = text.slice(a, i);
      const hit = text.slice(i, i + needle.length);
      const after = text.slice(i + needle.length, b);
      return ((a > 0 ? "…" : "") + before + "[" + hit + "]" + after + (b < text.length ? "…" : "")).replace(/\s+/g, " ");
    }
  }
  return text.slice(0, width).replace(/\s+/g, " ");
}
