# Remote & Integrations Layers

## Layer 4: `remote/`

User-facing stitching for the LAN flow.

* `server.py` — `OpendeskServer`. Accepts WebSocket connections, runs the
  right handshake (pair vs. auth), wraps each session in a `Peer` +
  `ComputerDispatcher`, tracks sessions in a `SessionRegistry`.
  `enable_pairing(code)` flips into one-shot pairing mode.

  **Single-controller policy:** at most one peer holds an active session
  at any moment. Same-peer reconnect bumps the previous session (the
  common case after a Wi-Fi blip or controller crash). A *different*
  peer hitting an active server gets rejected with `ErrorCode.BUSY` —
  carried on the HELLO frame so the client raises a clean
  `ProtocolError` instead of seeing a generic disconnect. Two
  controllers fighting over the same mouse / keyboard is the failure
  mode this prevents; multi-session / observer support is a phase-2
  design item with its own session-kind, not a numeric knob.
* `policy.py` — `Policy` Protocol + `AllowAllPolicy` (default) and
  `ConsolePolicy` (stdin-prompts gated methods, auto-allows observations).
  Server-side gate applied per call.
* `audit.py` — `AuditLog`. Append-only JSONL at
  `<home>/audit/YYYY-MM-DD.jsonl`. Records session lifecycle plus every
  protocol method invocation with outcome.
* `admin.py` — local Unix-socket (or Windows named-pipe) IPC. Used by
  `opendesk sessions`, `opendesk disconnect`, and `opendesk unpair` /
  `opendesk peers remove` (which both kick the active session via this
  channel when revoking trust). Local-only, no network exposure.
* `service.py` — generates systemd / launchd / Task Scheduler entries
  for `opendesk serve` (one-command install / uninstall).
* `discovery.py` — `advertise(name, port, public_key)` and `discover(timeout)`
  via Zeroconf. Service type `_opendesk._tcp.local.` with TXT records
  carrying the host's public key + fingerprint.
* `client.py` — `connect(target)` (auto-reconnect on transient drops by
  default) and `pair_with(host, port, code)`. Resolves a peer name →
  mDNS → WebSocket → `auth_client` → `RemoteComputer`. When no target
  is supplied, falls back to the persistent default peer
  (`opendesk peers default <name>`).

---

## Layer 5: `integrations/`

### `mcp.py`

Single MCP server. At list-tools time, augments Computer-use tool schemas
(`screenshot`, `mouse`, `keyboard`, `ui`, `app`, `clipboard`, `ocr`) with an
optional `peer` field; appends admin tools (`opendesk_peers`,
`opendesk_discover`, `opendesk_use`, `opendesk_status`,
`opendesk_capabilities`, `opendesk_disconnect`).

At call-tool time, strips `peer` from arguments, resolves to a `Computer`
via `MCPSession`, builds a fresh `ToolContext` with that Computer, and
dispatches. Local-only tools (`learn`, `schedule`, `audit`) are passed
through unchanged.

### `MCPSession` (`mcp_session.py`)

Per-MCP-session state: explicit default peer, cache of open
`RemoteComputer` connections, the local `LocalComputer`. Resolution order:

1. Per-call `peer:` argument
2. Explicit default from `opendesk_use`
3. Lone trusted peer (implicit default)
4. Multiple peers + no default → **error** (forces explicit choice)
5. No peers paired → local

### `claude_code.py`, `openai_compat.py`, `langchain_compat.py`

Adapters that present tools in the native format of each agent SDK.

---

Next: [Data Flow →](data-flow.md) — trace a call end-to-end, both locally and over the wire.
