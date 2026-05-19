# `audit` — Session Audit Log & Replay

```python
from opendesk.tools.audit import AuditTool
tool = AuditTool()
```

Records every action taken in the current session and lets you replay them exactly. Available in any MCP or agent session.

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `"show"` \| `"replay"` | `"show"` | `"show"` displays the log; `"replay"` re-executes the recorded actions |
| `format` | `"summary"` \| `"full"` | `"full"` | For `action="show"`: one-line count or full timestamped log |
| `session_id` | str | current session | Inspect/replay a specific session by ID |
| `skip_errors` | bool | `True` | For `action="replay"`: skip actions that originally errored |

## Show the log

```python
params = AuditTool.Params

# One-line summary
result = await tool.execute(ctx, params(action="show", format="summary"))
# session=default… | 5 actions: 2× mouse_click, 2× keyboard_type, 1× app_open

# Full timestamped log — ✓ marks replayable actions
result = await tool.execute(ctx, params(action="show", format="full"))
# Audit log — session 'default' (5 actions)
# [  1] [✓] [2026-05-19 15:01:14] app_open             {"action":"open","name":"TextEdit"}
# [  2] [✓] [2026-05-19 15:01:16] keyboard_type        {"action":"type"}
# [  3] [✓] [2026-05-19 15:01:18] mouse_click          {"action":"click","x":400,"y":300}
# [  4] [ ] [2026-05-19 15:01:20] screenshot           {}
# [  5] [✓] [2026-05-19 15:01:21] keyboard_press       {"action":"press"}
#
# ✓ = replayable via audit(action='replay')
```

## Replay

Re-executes every replayable action from the session in order. Read-only actions (screenshots, OCR, clipboard reads) are automatically skipped.

```python
result = await tool.execute(ctx, params(action="replay"))
# Replaying 4 action(s)...
#   OK    app_open             Opened 'TextEdit'.
#   OK    keyboard_type        Typed 11 characters: 'hello world'
#   OK    mouse_click          click at (400,300) done.
#   OK    keyboard_press       Pressed key: 'return'
#
# Done — 4 succeeded, 0 failed.
```

Include actions that originally errored:

```python
result = await tool.execute(ctx, params(action="replay", skip_errors=False))
```

## In MCP / Claude sessions

In Claude Code, Claude Desktop, Cursor, or any MCP client, just ask:

```
"Show me the audit log"
"Show audit summary"
"Replay everything from this session"
"Replay the session, including failed actions"
```

The agent will call the `audit` tool automatically.

## What gets replayed

| Action | Replayed |
|--------|----------|
| mouse (click, move, scroll, drag) | ✓ |
| keyboard (type, press, hotkey, hold) | ✓ |
| app (open, close, focus) | ✓ |
| clipboard write | ✓ |
| ui (click, type, press_key, click_menu) | ✓ |
| screenshot | skipped (read-only) |
| clipboard read | skipped (read-only) |
| OCR | skipped (read-only) |

!!! note
    Only actions recorded **in the current session** are replayable. The replay runs immediately — there is no delay between steps unless the original tool's `settle_ms` is included in the stored params.
