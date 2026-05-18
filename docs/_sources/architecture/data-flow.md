# Data Flow

## Local call

```
LLM
  │ tool_name + arguments
  ▼
Tool.parse_params  ← Pydantic
  ▼
ToolContext.check_permission
  ▼
Tool.execute(ctx, params)
  ▼
ctx.computer = LocalComputer
  ▼
mss / pyautogui / AppleScript / etc.
  ▼
ToolResult → Integration adapter → LLM
```

## Remote call

```
LLM
  │ tool_name + arguments (with optional peer:)
  ▼
MCPDispatcher: strips peer, resolves Computer
  ▼
Tool.execute(ctx, params)        ← ctx.computer = RemoteComputer
  ▼
peer.call("display.capture", ...)
  ▼
encode (msgpack) → AEAD encrypt → WebSocket binary frame → TCP
  ┌────────── over the wire ──────────┐
  ▼
TCP → WebSocket → AEAD decrypt → decode (msgpack)
  ▼
Peer dispatches → ComputerDispatcher
  ▼
LocalComputer on the remote machine
  ▼
result → encode → ... → back through wire → RemoteComputer
  ▼
ToolResult → Integration adapter → LLM
```

---

Want to extend this with your own tools? See [Adding a Custom Tool →](custom-tools.md)
