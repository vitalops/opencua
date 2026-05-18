# Session Lifecycle & Method Namespace

## 9. Session lifecycle

```
TCP connect
  │
  ▼
Handshake (pair or auth) → EncryptedConnection
  │
  ▼
HELLO exchange (one frame each direction, simultaneous)
  │  client.hello(my_manifest) ──► server.hello(my_manifest)
  │  client ◄── server HELLO (capabilities + optional error)
  │
  ▼  [server HELLO error → ProtocolError, connection closes]
  │
  ▼
peer.start()  ← spawns background recv loop
  │
  ├── outbound: peer.call(method, params) → result dict
  │                or peer.stream(method, params) → AsyncIterator
  │
  ├── inbound:  Dispatcher.call / Dispatcher.stream (server side)
  │
  ├── push:     peer.push(topic, payload)  /  peer.on_push(handler)
  │
  ▼
peer.aclose() or connection drop
  └── all in-flight calls fail with ConnectionClosed
```

**Single-controller invariant (server side):** at most one session exists at a
time. The `accept_lock` in `OpendeskServer._handle_connection` serialises the
"check-existing + register-new" step:

* Same peer reconnecting → old session evicted (`session.evicted` PUSH sent),
  new session registered.
* Different peer while one is active → HELLO sent with `error.code = "busy"`,
  connection closed.

---

## 10. Method namespace

Method names are `<namespace>.<verb>` strings. The full set dispatched by
`ComputerDispatcher`:

### Observation methods (auto-approved by ConsolePolicy)

| Method | Description |
|---|---|
| `display.capture` | Screenshot → Pixmap |
| `display.displays` | List monitors |
| `display.cursor_position` | Current pointer position |
| `display.subscribe` | Streaming display frames |
| `windows.list` | All windows |
| `windows.focused` | Focused window |
| `ui.tree` | Accessibility element tree |
| `clipboard.read` | Read clipboard contents |
| `fs.read` | Read file bytes |
| `fs.list` | List directory |
| `fs.stat` | File metadata |
| `process.list` | Running processes |
| `apps.list` | Installed / running applications |
| `notifications.list` | Recent notifications |
| `input.subscribe` | Streaming input events |
| `system.capabilities` | Capability manifest |
| `system.environment` | Environment variables + platform info |

### Action methods (require policy approval)

| Method | Description |
|---|---|
| `input.pointer` | Mouse move / click / drag / scroll |
| `input.key` | Key press / release / chord |
| `input.text` | Type a string |
| `apps.open` | Launch application |
| `apps.close` | Quit application |
| `apps.focus` | Bring application to front |
| `windows.focus` | Focus a specific window |
| `windows.move` | Reposition / resize a window |
| `windows.close` | Close a window |
| `ui.perform` | Perform accessibility action on element |
| `clipboard.write` | Write clipboard contents |
| `fs.write` | Write file bytes |
| `fs.delete` | Delete file or directory |
| `fs.move` | Move / rename file |
| `fs.mkdir` | Create directory |
| `process.shell` | Run shell command |
| `process.exec` | Run process directly (no shell) |
| `system.lock` | Lock the screen |

---

Last section: [Discovery, Admin IPC & Cryptographic Summary →](discovery.md)
