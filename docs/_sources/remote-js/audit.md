# Audit Log

`opendesk-js serve` writes one JSON line per event to
`~/.opendesk/audit/<YYYY-MM-DD>.jsonl` (directory `0700`, files `0600`).
Audit failures never crash the server.

Event types: `session.opened`, `session.closed`, `session.rejected`, `call`.

## CLI viewer

```bash
opendesk-js audit                        # today's entries
opendesk-js audit --date 2026-05-13      # a specific date
opendesk-js audit --peer mini            # filter by peer name
opendesk-js audit --limit 50            # last 50 entries
opendesk-js audit --follow               # tail -f style, polls every 500 ms
```

## Programmatic access

```typescript
import { AuditLog } from "@vitalops/opendesk-sdk";

const log = new AuditLog();                    // defaults to ~/.opendesk/audit/
const entries = log.iterEntries();             // today
const older   = log.iterEntries("2026-05-13");

for (const e of entries) {
  console.log(e.ts, e.type, e.peer?.name, e.method, e.outcome);
}
```

---

Next: [Trust & Security →](security.md)
