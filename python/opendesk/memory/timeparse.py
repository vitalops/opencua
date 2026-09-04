"""Human time-range parsing for screen-memory queries.

Agents (and humans) say things like "on Tuesday", "last week", "yesterday
afternoon", "2h", "2026-09-01".  :func:`parse_when` turns one such phrase
into a ``(start, end)`` pair of epoch seconds in the machine's local
timezone.  ``end`` is ``None`` when the phrase is open-ended ("since
Monday", "3d").

Supported forms
---------------
* Relative durations: ``"2h"``, ``"3d"``, ``"1w"``, ``"45m"`` → last N units.
* ``"today"``, ``"yesterday"``, ``"this week"``, ``"last week"``,
  ``"this month"``, ``"last month"``.
* Weekday names: ``"tuesday"`` → the most recent Tuesday (whole day).
  ``"last tuesday"`` is the Tuesday before that if today is Tuesday.
* Day-part modifiers: ``"yesterday morning"``, ``"tuesday afternoon"``,
  ``"monday evening"``, ``"last night"``.
* ISO dates / datetimes: ``"2026-09-01"``, ``"2026-09-01T14:30"``,
  ``"2026-09-01 14:30"``.  A bare date spans the whole day.
* ``"N days ago"``, ``"N hours ago"``, ``"an hour ago"``.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Optional, Tuple

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAY_ABBR = {d[:3]: d for d in _WEEKDAYS}

_DAY_PARTS = {
    "morning": (5, 12),
    "afternoon": (12, 17),
    "evening": (17, 22),
    "night": (20, 24),
}

_UNIT_SECONDS = {
    "s": 1, "sec": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "wk": 604800, "week": 604800, "weeks": 604800,
}

TimeRange = Tuple[float, Optional[float]]


def parse_when(text: str, *, now: Optional[_dt.datetime] = None) -> TimeRange:
    """Parse *text* into ``(start_epoch, end_epoch_or_None)``.

    Raises :class:`ValueError` when the phrase isn't understood.
    """
    now = now or _dt.datetime.now()
    t = (text or "").strip().lower()
    if not t:
        raise ValueError("empty time expression")

    # ISO datetime / date -------------------------------------------------
    iso = _parse_iso(t)
    if iso is not None:
        return iso

    # "2h", "3d", "1w", "45m", "90s"
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-z]+)", t)
    if m and m.group(2) in _UNIT_SECONDS:
        secs = float(m.group(1)) * _UNIT_SECONDS[m.group(2)]
        return (now.timestamp() - secs, None)

    # "3 days ago", "an hour ago", "2 weeks ago"
    m = re.fullmatch(r"(?:(\d+)|an?)\s+([a-z]+)\s+ago", t)
    if m and m.group(2) in _UNIT_SECONDS:
        n = int(m.group(1)) if m.group(1) else 1
        unit = m.group(2)
        secs = n * _UNIT_SECONDS[unit]
        point = now - _dt.timedelta(seconds=secs)
        if _UNIT_SECONDS[unit] >= 86400:
            # Whole day, N days ago.
            return _day_range(point.date())
        return (point.timestamp(), None)

    # "last N days/hours/weeks", "past 2 hours"
    m = re.fullmatch(r"(?:last|past)\s+(\d+)\s+([a-z]+)", t)
    if m and m.group(2) in _UNIT_SECONDS:
        secs = int(m.group(1)) * _UNIT_SECONDS[m.group(2)]
        return (now.timestamp() - secs, None)

    # Named periods ---------------------------------------------------------
    today = now.date()
    if t in ("today", "since today"):
        return _day_range(today)
    if t in ("yesterday", "since yesterday"):
        return _day_range(today - _dt.timedelta(days=1))
    if t == "last night":
        return _part_range(today - _dt.timedelta(days=1), "night")
    if t in ("this week", "since monday"):
        start = today - _dt.timedelta(days=today.weekday())
        return (_midnight(start).timestamp(), None)
    if t == "last week":
        this_monday = today - _dt.timedelta(days=today.weekday())
        last_monday = this_monday - _dt.timedelta(days=7)
        return (_midnight(last_monday).timestamp(), _midnight(this_monday).timestamp())
    if t == "this month":
        return (_midnight(today.replace(day=1)).timestamp(), None)
    if t == "last month":
        first_this = today.replace(day=1)
        last_month_end = first_this - _dt.timedelta(days=1)
        first_last = last_month_end.replace(day=1)
        return (_midnight(first_last).timestamp(), _midnight(first_this).timestamp())
    if t == "this year":
        return (_midnight(today.replace(month=1, day=1)).timestamp(), None)

    # "<weekday>", "last <weekday>", "on <weekday>", with optional day part
    m = re.fullmatch(
        r"(?:on\s+|last\s+|this\s+)?([a-z]+)(?:\s+(morning|afternoon|evening|night))?", t
    )
    if m:
        name = m.group(1)
        name = _WEEKDAY_ABBR.get(name, name)
        if name in _WEEKDAYS:
            target = _WEEKDAYS.index(name)
            delta = (today.weekday() - target) % 7
            if delta == 0 and t.startswith("last "):
                delta = 7
            day = today - _dt.timedelta(days=delta)
            if m.group(2):
                return _part_range(day, m.group(2))
            return _day_range(day)

    # "yesterday morning", "today afternoon"
    m = re.fullmatch(r"(today|yesterday)\s+(morning|afternoon|evening|night)", t)
    if m:
        day = today if m.group(1) == "today" else today - _dt.timedelta(days=1)
        return _part_range(day, m.group(2))

    raise ValueError(
        f"Cannot parse time expression: {text!r}. "
        "Try '2h', '3d', 'yesterday', 'tuesday', 'last week', 'this month', "
        "or an ISO date like 2026-09-01."
    )


def parse_range(
    since: Optional[str], until: Optional[str], *, now: Optional[_dt.datetime] = None,
) -> TimeRange:
    """Combine optional ``since`` / ``until`` phrases into one range.

    ``since`` alone uses the phrase's own end if it has one ("tuesday" →
    that whole day).  ``until`` alone bounds the top only.  Both together
    take ``since``'s start and ``until``'s end (or start, when the ``until``
    phrase is open-ended, e.g. "until yesterday" = before yesterday's start).
    """
    start: float = 0.0
    end: Optional[float] = None
    if since:
        s_start, s_end = parse_when(since, now=now)
        start, end = s_start, s_end
    if until:
        u_start, u_end = parse_when(until, now=now)
        end = u_end if u_end is not None else u_start
        if since is None:
            start = 0.0
    if end is not None and end <= start:
        raise ValueError("'until' is not after 'since' — the window is empty")
    return (start, end)


# ---------------------------------------------------------------------------


def _parse_iso(t: str) -> Optional[TimeRange]:
    try:
        d = _dt.date.fromisoformat(t)
        return _day_range(d)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dt%H:%M:%S", "%Y-%m-%dt%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = _dt.datetime.strptime(t, fmt)
            return (dt.timestamp(), None)
        except ValueError:
            continue
    return None


def _midnight(day: _dt.date) -> _dt.datetime:
    return _dt.datetime.combine(day, _dt.time.min)


def _day_range(day: _dt.date) -> TimeRange:
    start = _midnight(day)
    return (start.timestamp(), (start + _dt.timedelta(days=1)).timestamp())


def _part_range(day: _dt.date, part: str) -> TimeRange:
    h0, h1 = _DAY_PARTS[part]
    start = _midnight(day) + _dt.timedelta(hours=h0)
    end = _midnight(day) + _dt.timedelta(hours=h1)
    return (start.timestamp(), end.timestamp())


def fmt_ts(ts: float) -> str:
    """Local ``YYYY-MM-DD HH:MM:SS`` for display."""
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def fmt_range(start: float, end: Optional[float]) -> str:
    if start <= 0 and end is None:
        return "all time"
    left = fmt_ts(start) if start > 0 else "beginning"
    right = fmt_ts(end) if end is not None else "now"
    return f"{left} → {right}"
