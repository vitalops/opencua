# MCP Integration

Run `opendesk install` on the controller as before. The MCP server now exposes:

**Computer-use tools** — `screenshot`, `mouse`, `keyboard`, `ui`, `app`,
`clipboard`, `ocr`. Each accepts an optional `peer:` argument.

**Admin tools** — for the agent to self-administer:

| Tool | Purpose |
|---|---|
| `opendesk_peers` | List `local` + every trusted peer, marked with `[default (explicit/implicit)]` and `[active]`. |
| `opendesk_discover` | Browse the LAN for opendesk peers (paired and unpaired). |
| `opendesk_use <peer>` | Set the default peer for subsequent calls. `peer="local"` reverts. |
| `opendesk_status` | Show the effective default peer and open connections. |
| `opendesk_capabilities [peer]` | Capability manifest of a peer. |
| `opendesk_disconnect [peer]` | Close cached connection(s). |

## Default peer resolution

When a tool call omits `peer:`, opendesk picks one in this order:

1. The explicit default set via `opendesk_use`.
2. The lone trusted peer, if exactly one exists.
3. Otherwise — if multiple peers are paired with no default —
   **an error is returned** asking the agent to be explicit.
4. If no peers are paired at all, falls through to the local machine.

The ambiguous case (#3) deliberately errors instead of silently falling
back to local: a tool call that *meant* to land on a remote should never
end up on the host machine because the agent forgot.

## Agent example

```
> opendesk_peers
Available peers:
  local
  mini  [default (implicit)]  (9c2f:1abc:b3d4:8870)
  desk                        (3a48:7f0c:2211:0099)

> screenshot
ERROR: Multiple peers paired (mini, desk) and no default set.
Run `opendesk_use <name>` to choose one, or pass `peer:` on this call
(use 'local' to target this machine).

> opendesk_use mini
Default peer is now: mini

> screenshot
[on mini] Captured 1920×1200 screenshot...
```

## Why pairing isn't an MCP tool

The 6-digit code authenticates the pairing handshake. Codes should be
typed by a human looking at the controlled machine's screen, not flow
through the LLM channel where they might be logged, leaked, or replayed.
`opendesk pair` / `opendesk pair-with` are CLI-only by design.

---

Next: [Trust & Security →](security.md)
