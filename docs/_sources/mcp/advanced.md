# Advanced: In-process Server & Restrictions

## Running the MCP server in Python

If you need to embed the server in your own process or customize permissions:

```python
import asyncio
from mcp.server.stdio import stdio_server
from opendesk.integrations.mcp import create_mcp_server
from opendesk.registry import create_registry
from opendesk.tools.base import ToolContext, PermissionDeniedError

# Custom permission policy: block any app launch
async def my_policy(tool: str, argument: str, description: str) -> None:
    if tool == "app" and "open" in argument:
        raise PermissionDeniedError("App launching is disabled in this session.")

ctx = ToolContext(session_id="my-session", permission_handler=my_policy)
server = create_mcp_server(registry=create_registry(), ctx=ctx)

async def main():
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())

asyncio.run(main())
```

---

## Restricting what the agent can do

Lock down the sandbox before starting the server:

```python
from opendesk.computer.sandbox import configure_sandbox
from opendesk.tools.base import ToolContext

# Only allow Safari and Terminal; only act within the left half of the screen
configure_sandbox(
    session_id="restricted",
    allowed_apps=["Safari", "Terminal"],
    screen_region=(0, 0, 1280, 1600),
)

ctx = ToolContext(session_id="restricted")
server = create_mcp_server(ctx=ctx)
```

All screenshots and mouse clicks outside `(0,0,1280,1600)` will be rejected with an error
the LLM sees, so it knows to stay in bounds.

---

## Audit log

Every action is recorded in the session sandbox:

```python
from opendesk.computer.sandbox import get_sandbox

sandbox = get_sandbox("my-session")
print(sandbox.summary())
# session=my-session… | 12 actions: 3x screenshot, 4x ui_action, 2x keyboard_type, 3x mouse_click

log = sandbox.export_audit_log()
# [{"id": "...", "timestamp": 1234567890.0, "action": "screenshot", "params": {...}, ...}, ...]
```

### Custom permission handler with MCP

```python
from opendesk.tools.base import ToolContext, PermissionDeniedError

BLOCKED_APPS = {"System Preferences", "Keychain Access"}

async def policy(tool: str, argument: str, description: str) -> None:
    for blocked in BLOCKED_APPS:
        if blocked.lower() in argument.lower():
            raise PermissionDeniedError(f"Blocked: {blocked}")

ctx = ToolContext(session_id="mcp", permission_handler=policy)
server = create_mcp_server(ctx=ctx)
```

---

Something not working? Check the [Troubleshooting guide →](troubleshooting.md)
