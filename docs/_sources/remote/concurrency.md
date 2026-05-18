# Concurrency: One Controller at a Time

`opendesk serve` enforces **single-controller** by design — at most one
controller machine drives the desktop at a time. Multiple controllers
fighting over a single mouse / keyboard / focused window is the failure
mode this policy prevents.

* **Different peer trying to connect while one is active** → rejected
  with a clean `BUSY` error. The client sees a `ProtocolError`
  ("server is busy: laptop is the active controller…") so the agent
  knows to back off or ask the operator.
* **Same peer reconnecting** (Wi-Fi blip, controller crash, network
  roam) → bumps the previous session cleanly. No need to wait out the
  stale TCP connection.

You can still pair as many controllers as you like — they just take
turns. To free the slot:

```
opendesk sessions             # show who's active (0 or 1)
opendesk disconnect           # ask the active controller to leave (cooperative)
opendesk unpair NAME          # revoke trust entirely (enforced)
```

## Disconnect vs unpair — the trust model

Both commands talk to the running daemon over a local Unix socket /
named pipe (`~/.opendesk/admin.sock`), not the network — only the user
running `serve` can use them. They differ in *who has the last word*:

* **`opendesk disconnect`** is **cooperative**. The server sends a
  `session.evicted` PUSH frame and closes the connection. A cooperative
  client (`RemoteComputer`) sees the frame, suppresses its auto-reconnect,
  and raises `SessionEvicted` on subsequent calls. Trust is preserved —
  the same controller can come back when it decides to reconnect. A hostile
  client could ignore the PUSH and reconnect immediately; for that case,
  use `unpair`.
* **`opendesk unpair NAME`** is **enforced**. The peer is removed
  from the trusted-peers store, then disconnected (via the same admin
  IPC channel as `disconnect`). Their next reconnect attempt fails
  authentication outright.

In short: `disconnect` says *"please leave, you can come back later"*;
`unpair` says *"you are not welcome here anymore."*

---

Next: [Programmatic Use →](programmatic.md) — connect from Python or JS code directly.
