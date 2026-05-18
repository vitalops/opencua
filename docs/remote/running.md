# Running

## Controlled machine

```bash
opendesk serve
```

Long-running daemon. Accepts paired peers only — refuses anyone whose
static key isn't in `trusted-peers.json`. Logs each connection /
disconnection to stderr. Survive reboots: see [Service Install](service.md).

## Controller

The agent uses opendesk through the existing MCP server — pointing
Claude Code (or whatever) at `opendesk-mcp` just works. See [MCP Integration](mcp.md).

For ad-hoc smoke tests:

```bash
opendesk connect mini
# Connected to mini  backend=local/darwin
# Capabilities: ['apps.lifecycle', 'clipboard.read', 'clipboard.write', ...]

opendesk connect mini --screenshot ~/Desktop/mini-screen.png
```

---

Next: [MCP Integration →](mcp.md) — how the agent picks which machine to act on.
