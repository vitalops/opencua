# Concurrency: One Controller at a Time

`opendesk-js serve` enforces **single-controller** by design — at most one
controller machine drives the desktop at a time. Multiple controllers fighting
over a single mouse / keyboard / focused window is the failure mode this
policy prevents.

- **Different peer trying to connect while one is active** → rejected with a
  `BUSY` error.
- **Same peer reconnecting** (Wi-Fi blip, crash, network roam) → bumps the
  previous session cleanly.

To free the slot:

```bash
opendesk-js sessions           # show who's active
opendesk-js disconnect         # ask the active controller to leave (cooperative)
opendesk-js unpair NAME        # revoke trust entirely (enforced)
```

## disconnect vs unpair

Both commands talk to the running daemon over a local Unix socket /
named pipe (`~/.opendesk/admin.sock`) — only the user running `serve` can
issue them.

- **`opendesk-js disconnect`** is **cooperative**. The server sends a
  `session.evicted` PUSH frame and closes the connection. A cooperative client
  (`RemoteComputer`) sees the frame, suppresses auto-reconnect, and raises
  `SessionEvicted` on subsequent calls. Trust is preserved — the same
  controller can come back later.
- **`opendesk-js unpair NAME`** is **enforced**. The peer is removed from
  the trusted-peers store, then disconnected. Their next reconnect attempt
  fails authentication outright.

In short: `disconnect` says *"please leave, you can come back later"*;
`unpair` says *"you are not welcome here anymore."*

---

Next: [Programmatic Use →](programmatic.md) — connect, pair, and serve from Node.js code directly.
