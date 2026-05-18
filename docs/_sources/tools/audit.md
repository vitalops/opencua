# `audit` — Session Audit Log

```python
from opendesk.tools.audit import AuditTool
tool = AuditTool()
```

Returns the audit log for the current session — every action recorded by the sandbox. Available in any MCP or agent session without programmatic access to the sandbox object.

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `format` | `"summary"` \| `"full"` | `"full"` | `"summary"` returns a one-line action count; `"full"` returns the complete timestamped log |
| `session_id` | str | current session | Inspect a specific session by ID; defaults to the session making the request |

## Examples

```python
params = AuditTool.Params

# One-line summary
result = await tool.execute(ctx, params(format="summary"))
print(result.output)
# session=default… | 8 actions: 3× screenshot, 3× mouse_click, 2× keyboard_type

# Full timestamped log
result = await tool.execute(ctx, params(format="full"))
print(result.output)
# Audit log — session 'default' (8 actions)
# [2026-05-10 15:01:14] screenshot          {}
# [2026-05-10 15:01:16] mouse_click         {"x": 400, "y": 300}
# ...

# Inspect a different session
result = await tool.execute(ctx, params(format="full", session_id="restricted"))
```

## In MCP sessions

In Claude Code, Claude Desktop, Cursor, or any MCP client, just ask:

```
"Show me the audit log"
"Show audit summary"
"What actions have been taken this session?"
```

The agent will call the `audit` tool automatically.

---

Next: [learn →](learn.md) — record a workflow once, replay it any time.
