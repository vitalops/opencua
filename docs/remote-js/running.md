# Running

## Controlled machine

```bash
opendesk-js serve
```

Long-running daemon. Accepts paired peers only — refuses anyone whose static
key isn't in `trusted-peers.json`. Logs each connection / disconnection to
stderr. Writes one JSONL audit line per event to
`~/.opendesk/audit/<YYYY-MM-DD>.jsonl`.

Options:

```
opendesk-js serve [--port N] [--host H] [--no-mdns] [--approve {auto|console}]
```

- `--approve console` — prompts on stderr for every tool call instead of
  auto-approving. Requires a TTY; falls back to auto if stdin is not a TTY.
- `--no-mdns` — disable mDNS advertisement (useful in isolated environments).

## Controller

The agent uses opendesk through the existing MCP server — pointing Claude Code
at `opendesk-js mcp` just works. See [MCP Integration](mcp.md).

For ad-hoc smoke tests:

```bash
npx opendesk-js connect mini
# Connected to mini  backend=local
# Screenshot: OK (received image data)
```

---

Next: [MCP Integration →](mcp.md) — peer resolution, admin tools, and agent examples.
