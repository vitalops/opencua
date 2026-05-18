# Custom / Generic Integration

If your framework isn't listed, use the raw tool API directly:

```python
import json
from opendesk.registry import create_registry
from opendesk.tools.base import allow_all_context

registry = create_registry()
ctx = allow_all_context()

# Get JSON schemas for all tools (to pass to your LLM)
tool_schemas = [
    {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.get_schema(),
    }
    for tool in registry.tools()
]

# Execute a tool from LLM output
tool_name = "screenshot"       # from LLM response
arguments = {"marks": True}    # from LLM response

tool = registry.get(tool_name)
params = tool.parse_params(arguments)
result = await tool.execute(ctx, params)

print(result.output)
for att in result.attachments:
    print(f"Attachment: {att.filename} ({att.media_type}, {len(att.content)} bytes)")
```

---

## Sandbox configuration

Restrict what the agent can do in a session:

```python
from opendesk.computer.sandbox import configure_sandbox, get_sandbox
from opendesk.tools.base import ToolContext

# Only allow specific apps and a screen region
configure_sandbox(
    session_id="restricted",
    allowed_apps=["Safari", "Terminal"],
    screen_region=(0, 0, 1280, 800),  # (x, y, width, height)
)

ctx = ToolContext(session_id="restricted")
# Now the agent can only screenshot/click within x:0–1280, y:0–800
# and can only open/focus Safari or Terminal.

# Audit everything that happened
sandbox = get_sandbox("restricted")
print(sandbox.summary())
log = sandbox.export_audit_log()  # list of dicts
```

---

That covers all integrations. From here you might want the [Tools reference →](../tools/index.md) for full parameter docs, or [Architecture →](../architecture/index.md) to understand how the layers fit together.
