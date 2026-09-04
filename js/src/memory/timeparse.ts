/**
 * Human time-range parsing for screen-memory queries — mirrors Python's
 * opendesk/memory/timeparse.py.
 *
 * `parseWhen("tuesday afternoon")` → `[startEpochSeconds, endEpochSeconds | null]`
 * in the machine's local timezone.  `end` is null for open-ended phrases
 * ("2h", "since monday").
 */

const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const WEEKDAY_ABBR: Record<string, string> = Object.fromEntries(WEEKDAYS.map((d) => [d.slice(0, 3), d]));

const DAY_PARTS: Record<string, [number, number]> = {
  morning: [5, 12],
  afternoon: [12, 17],
  evening: [17, 22],
  night: [20, 24],
};

const UNIT_SECONDS: Record<string, number> = {
  s: 1, sec: 1, second: 1, seconds: 1,
  m: 60, min: 60, minute: 60, minutes: 60,
  h: 3600, hr: 3600, hour: 3600, hours: 3600,
  d: 86400, day: 86400, days: 86400,
  w: 604800, wk: 604800, week: 604800, weeks: 604800,
};

export type TimeRange = [number, number | null];

// JS weekday: 0 = Sunday.  Convert to Python-style 0 = Monday.
function pyWeekday(d: Date): number {
  return (d.getDay() + 6) % 7;
}

function midnight(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}

function sec(d: Date): number {
  return d.getTime() / 1000;
}

function dayRange(d: Date): TimeRange {
  const start = midnight(d);
  return [sec(start), sec(addDays(start, 1))];
}

function partRange(d: Date, part: string): TimeRange {
  const [h0, h1] = DAY_PARTS[part];
  const base = midnight(d);
  const start = new Date(base.getTime() + h0 * 3600_000);
  const end = new Date(base.getTime() + h1 * 3600_000);
  return [sec(start), sec(end)];
}

function parseIso(t: string): TimeRange | null {
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t);
  if (m) {
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    if (isNaN(d.getTime())) return null;
    return dayRange(d);
  }
  m = /^(\d{4})-(\d{2})-(\d{2})[t ](\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(t);
  if (m) {
    const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], m[6] ? +m[6] : 0);
    if (isNaN(d.getTime())) return null;
    return [sec(d), null];
  }
  return null;
}

/** Parse *text* into `[start, end|null]` epoch seconds.  Throws on junk. */
export function parseWhen(text: string, now: Date = new Date()): TimeRange {
  const t = (text ?? "").trim().toLowerCase();
  if (!t) throw new Error("empty time expression");

  const iso = parseIso(t);
  if (iso) return iso;

  // "2h", "3d", "1w", "45m"
  let m = /^(\d+(?:\.\d+)?)\s*([a-z]+)$/.exec(t);
  if (m && UNIT_SECONDS[m[2]] !== undefined) {
    return [sec(now) - parseFloat(m[1]) * UNIT_SECONDS[m[2]], null];
  }

  // "3 days ago", "an hour ago"
  m = /^(?:(\d+)|an?)\s+([a-z]+)\s+ago$/.exec(t);
  if (m && UNIT_SECONDS[m[2]] !== undefined) {
    const n = m[1] ? parseInt(m[1], 10) : 1;
    const unit = UNIT_SECONDS[m[2]];
    const point = new Date(now.getTime() - n * unit * 1000);
    if (unit >= 86400) return dayRange(point);
    return [sec(point), null];
  }

  // "last 3 days", "past 2 hours"
  m = /^(?:last|past)\s+(\d+)\s+([a-z]+)$/.exec(t);
  if (m && UNIT_SECONDS[m[2]] !== undefined) {
    return [sec(now) - parseInt(m[1], 10) * UNIT_SECONDS[m[2]], null];
  }

  const today = midnight(now);
  if (t === "today" || t === "since today") return dayRange(today);
  if (t === "yesterday" || t === "since yesterday") return dayRange(addDays(today, -1));
  if (t === "last night") return partRange(addDays(today, -1), "night");
  if (t === "this week" || t === "since monday") {
    return [sec(addDays(today, -pyWeekday(today))), null];
  }
  if (t === "last week") {
    const thisMonday = addDays(today, -pyWeekday(today));
    return [sec(addDays(thisMonday, -7)), sec(thisMonday)];
  }
  if (t === "this month") {
    return [sec(new Date(today.getFullYear(), today.getMonth(), 1)), null];
  }
  if (t === "last month") {
    const firstThis = new Date(today.getFullYear(), today.getMonth(), 1);
    const firstLast = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    return [sec(firstLast), sec(firstThis)];
  }
  if (t === "this year") return [sec(new Date(today.getFullYear(), 0, 1)), null];

  // "<weekday>", "last <weekday>", "on <weekday>" (+ optional day part)
  m = /^(?:on\s+|last\s+|this\s+)?([a-z]+)(?:\s+(morning|afternoon|evening|night))?$/.exec(t);
  if (m) {
    const name = WEEKDAY_ABBR[m[1]] ?? m[1];
    if (WEEKDAYS.includes(name)) {
      const target = WEEKDAYS.indexOf(name);
      let delta = (pyWeekday(today) - target + 7) % 7;
      if (delta === 0 && t.startsWith("last ")) delta = 7;
      const day = addDays(today, -delta);
      return m[2] ? partRange(day, m[2]) : dayRange(day);
    }
  }

  m = /^(today|yesterday)\s+(morning|afternoon|evening|night)$/.exec(t);
  if (m) {
    const day = m[1] === "today" ? today : addDays(today, -1);
    return partRange(day, m[2]);
  }

  throw new Error(
    `Cannot parse time expression: '${text}'. Try '2h', '3d', 'yesterday', 'tuesday', ` +
    "'last week', 'this month', or an ISO date like 2026-09-01.",
  );
}

/** Combine optional since / until phrases into one range. */
export function parseRange(since?: string | null, until?: string | null, now: Date = new Date()): TimeRange {
  let start = 0;
  let end: number | null = null;
  if (since) {
    [start, end] = parseWhen(since, now);
  }
  if (until) {
    const [uStart, uEnd] = parseWhen(until, now);
    end = uEnd !== null ? uEnd : uStart;
    if (!since) start = 0;
  }
  if (end !== null && end <= start) {
    throw new Error("'until' is not after 'since' — the window is empty");
  }
  return [start, end];
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** Local `YYYY-MM-DD HH:MM:SS`. */
export function fmtTs(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Local `YYYY-MM-DD`. */
export function fmtDay(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function fmtRange(start: number, end: number | null): string {
  if (start <= 0 && end === null) return "all time";
  const left = start > 0 ? fmtTs(start) : "beginning";
  const right = end !== null ? fmtTs(end) : "now";
  return `${left} → ${right}`;
}
