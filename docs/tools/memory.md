# `memory` — Screen Memory (searchable desktop history)

A low-frequency background loop captures your screen, runs OCR **locally**, and stores the text plus a small thumbnail in `~/.opendesk/memory`. The agent then gets a recall tool over that history.

Nothing leaves the machine. There is no upload, no cloud index, no telemetry.

```python
from opendesk.tools.memory import MemoryTool
tool = MemoryTool()
```

```typescript
import { OpenDeskClient } from "@vitalops/opendesk-sdk";
const client = new OpenDeskClient();
await client.memory({ action: "search", query: "invoice", since: "last week" });
```

## Ask Claude

> "What was the error message I saw in the terminal on Tuesday?"

> "Find the invoice number I had open last week"

> "Show me every time I opened that Grafana dashboard this month"

> "What was on my screen around 3pm yesterday?"

> "Pause screen memory for an hour"

> "Never record 1Password or anything with 'bank' in the title"

> "How much space is screen memory using?"

---

## Start recording

The tool only *reads* the index. Capture happens in a separate daemon:

**Python**

```bash
pip install 'opendesk[memory]'      # core capture deps + pynput for the pause hotkey
opendesk memory start                # foreground, Ctrl-C to stop
```

**JavaScript / TypeScript**

```bash
npm install @vitalops/opendesk-sdk
npm install uiohook-napi             # optional: enables the global pause hotkey
npx opendesk-js memory start         # foreground, Ctrl-C to stop
```

To run it at login as a user service (launchd / systemd --user / Task Scheduler):

```bash
opendesk memory install-service          # Python
npx opendesk-js memory install-service   # JavaScript
```

On first run on macOS the native Vision OCR helper is compiled once (about a minute) into `~/.opendesk/bin`; both SDKs share that binary and each frame then takes well under a second. Python uses `pytesseract` instead when it is installed; the JS SDK falls back to the bundled `tesseract.js` on Linux (it downloads its English model once and caches it under `~/.opendesk/memory/tessdata`).

```bash
opendesk memory status
```

```
opendesk screen memory
  daemon:    running (pid 41233, last tick: captured)
  capture:   active
  store:     /Users/you/.opendesk/memory
  frames:    1,842  (2026-08-30 09:12:04 → 2026-09-05 11:40:31)
  size:      61.3 MB of 2048 MB cap
  retention: 30 days   interval: every 30s
  deny list: 1Password, Bitwarden, KeePassXC, Keychain Access, LastPass, Dashlane
  hotkey:    <cmd>+<shift>+<alt>+p
  search:    FTS5
```

---

## Privacy controls

| Control | How |
|---|---|
| **Per-app deny list** | `opendesk memory deny add "Signal"` — matched case-insensitively against the frontmost app name *and* window title. Password managers are excluded by default. A match skips the capture entirely; no pixels are read. |
| **Pause hotkey** | `Cmd+Shift+Alt+P` on macOS, `Ctrl+Shift+Alt+P` elsewhere. Toggles capture from anywhere. Change it with `opendesk memory config --hotkey '<ctrl>+<alt>+m'`. |
| **Pause for a while** | `opendesk memory pause 2h`, or ask the agent: `memory(action="pause", duration="2h")`. |
| **Storage cap** | Default 2 GB. When exceeded, the oldest frames are deleted first until usage is back under 90% of the cap. |
| **Retention** | Default 30 days. Older frames are deleted on every housekeeping pass. |
| **Delete** | `opendesk memory clear --before 7d`, `--app Chrome`, or everything. The agent's `delete` action requires a scope and `confirm=true`. |
| **Duplicate suppression** | Visually identical consecutive frames (idle screen) are not stored. |

Everything under `~/.opendesk/memory` is created with mode `0600`/`0700`.

---

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `action` | `search` \| `show` \| `timeline` \| `status` \| `pause` \| `resume` \| `deny` \| `config` \| `delete` | required | What to do |
| `query` | str | null | Search terms (`search`). All terms must match; quote phrases; `invoice*` for prefix |
| `since` | str | null | Start of the window: `2h`, `yesterday`, `tuesday`, `last week`, `this month`, `yesterday afternoon`, `2026-09-01` |
| `until` | str | null | End of the window (same formats) |
| `app` | str | null | Only frames whose app name or window title contains this |
| `limit` | int | 15 | Max results for `search` / `timeline` |
| `id` | int | null | Frame id (`show`) |
| `include_image` | bool | true | Attach the thumbnail on `show` |
| `duration` | str | null | `pause` length, e.g. `30m`, `2h` (omit = until resumed) |
| `deny_add` / `deny_remove` | str | null | Deny-list edits (`deny`) |
| `interval_seconds` / `storage_cap_mb` / `retention_days` | number | null | Settings (`config`) |
| `confirm` | bool | false | Required for `delete` |

### Time phrases

| Phrase | Meaning |
|---|---|
| `2h`, `45m`, `3d`, `1w` | The last N hours / minutes / days / weeks |
| `today`, `yesterday`, `last night` | Whole day (or the evening) |
| `tuesday`, `last tuesday` | Most recent Tuesday; the one before if today is Tuesday |
| `tuesday afternoon`, `yesterday morning` | Day parts: morning 05–12, afternoon 12–17, evening 17–22, night 20–24 |
| `this week`, `last week`, `this month`, `last month` | Calendar periods |
| `3 days ago`, `an hour ago` | Relative points |
| `2026-09-01`, `2026-09-01 14:30` | ISO date (whole day) or datetime |

---

## SDK examples

```python
params = MemoryTool.Params

# "What was the error in the terminal on Tuesday?"
r = await tool.execute(ctx, params(action="search", query="error", app="Terminal", since="tuesday"))
print(r.output)
# Screen memory search 'error' — 2026-09-01 00:00:00 → 2026-09-02 00:00:00 — app~'Terminal'
# 2 match(es), best first:
#   #1412   2026-09-01 16:42:10  [Terminal] — zsh
#           npm ERR! code ELIFECYCLE … [Error]: connect ECONNREFUSED 127.0.0.1:5432
#   #1409   2026-09-01 16:40:31  [Terminal] — zsh
#           … [error] TS2345: Argument of type …

# Read one moment in full (text + thumbnail attachment)
r = await tool.execute(ctx, params(action="show", id=1412))
r.attachments[0].media_type  # "image/jpeg"

# "Every time the dashboard was open this month"
r = await tool.execute(ctx, params(action="timeline", app="Grafana", since="this month", limit=50))

# Privacy controls
await tool.execute(ctx, params(action="pause", duration="1h"))
await tool.execute(ctx, params(action="deny", deny_add="bank"))
await tool.execute(ctx, params(action="config", storage_cap_mb=512, retention_days=14))
await tool.execute(ctx, params(action="delete", since="yesterday", app="Slack", confirm=True))
```

### JavaScript / TypeScript

```typescript
import { OpenDeskClient } from "@vitalops/opendesk-sdk";
const client = new OpenDeskClient();

// "What was the error in the terminal on Tuesday?"
const r = await client.memory({ action: "search", query: "error", app: "Terminal", since: "tuesday" });
console.log(r.output);

// Full text + thumbnail (image/jpeg Buffer)
const moment = await client.memory({ action: "show", id: (r.metadata.ids as number[])[0] });

// "Every time the dashboard was open this month"
await client.memory({ action: "timeline", app: "Grafana", since: "this month", limit: 50 });

// Privacy controls
await client.memory({ action: "pause", duration: "1h" });
await client.memory({ action: "deny", denyAdd: "bank" });
await client.memory({ action: "config", storageCapMb: 512, retentionDays: 14 });
await client.memory({ action: "delete", since: "yesterday", app: "Slack", confirm: true });
```

JS parameter names are camelCase (`denyAdd`, `includeImage`, `storageCapMb`, …); everything else matches the table above.

Lower-level access:

```typescript
import { MemoryStore, parseWhen, ScreenMemoryRecorder } from "@vitalops/opendesk-sdk";

const store = new MemoryStore();
const [start, end] = parseWhen("last week");
for (const f of store.search("invoice", { start, end })) console.log(f.ts, f.app, f.title, f.snippet);

// Embed the capture loop in your own process
const rec = new ScreenMemoryRecorder({ intervalOverride: 60 });
await rec.run();   // rec.stop() to end
```

### Direct store access (Python)

```python
from opendesk.memory import MemoryStore, parse_when

with MemoryStore() as store:
    start, end = parse_when("last week")
    for frame in store.search("invoice", start=start, end=end):
        print(frame.when, frame.app, frame.title, frame.snippet)
        jpeg = store.thumbnail(frame)
```

---

## CLI reference

The JS CLI mirrors every command as `opendesk-js memory …`.

```
opendesk memory start [--interval N] [--log-file PATH]   run the capture daemon
opendesk memory status                                   daemon / storage / config
opendesk memory pause [30m|2h]                           pause (optionally for a duration)
opendesk memory resume
opendesk memory search "<query>" [--since ..] [--until ..] [--app ..] [--limit N]
opendesk memory timeline [--since ..] [--app ..]
opendesk memory show <id>
opendesk memory deny [list|add <pattern>|remove <pattern>]
opendesk memory config [--interval N] [--cap MB] [--retention DAYS] [--hotkey '<ctrl>+<alt>+m']
opendesk memory clear [--before <when>] [--app ..] [-y]
opendesk memory install-service | uninstall-service
```

All commands accept `--home DIR` (default `~/.opendesk`).

---

## How it works

```
every 30s ─► paused? ──► frontmost app / title ──► deny list? ──► capture
                                                                  │
          thumbnail (JPEG ≤480px) ◄── duplicate of last frame? ◄──┘
                 │
          OCR (pytesseract → macOS Vision → Windows WinRT)
                 │
          SQLite index.db  ──  FTS5 full-text over text + app + title
          thumbs/YYYY-MM-DD/<id>.jpg
                 │
          housekeeping: retention_days, storage_cap_mb (oldest first)
```

- **Storage** — Python: `~/.opendesk/memory/index.db` (SQLite, WAL) and `thumbs/`. JS: `~/.opendesk/memory/frames/YYYY-MM-DD.jsonl` (no native modules) and the same `thumbs/` layout. Both share `config.json`, `paused.json`, and the daemon heartbeat, so the deny list and pause state apply to either daemon. Run only one daemon per home. A typical frame is 20–40 KB; at one frame per 30 s across an 8-hour day that is roughly 30 MB/day *before* duplicate suppression.
- **Search** — Python: FTS5 with BM25 ranking and highlighted snippets (falls back to `LIKE` without FTS5). JS: scans only the day files inside the time window and ranks by term hits with title/app boosts. In both, all terms are ANDed; if nothing matches, the query is retried as OR so near-misses still surface.
- **Processes** — the daemon and the agent's MCP server are separate processes. Pause state, config, and a daemon heartbeat are shared through small JSON files, so the tool's `status` can tell you whether capture is actually running.
- **Local only** — the tool always reads the local index and is deliberately not peer-routable.

---

Next: [audit →](audit.md) — inspect everything the agent has done this session.
