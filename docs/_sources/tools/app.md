# `app` — Application Control

```python
from opendesk.tools.app import AppTool
tool = AppTool()
```

## Actions

| Action | Parameters | Description |
|--------|-----------|-------------|
| `open` | `name` | Launch application by name or path |
| `close` | `name` | Quit application |
| `focus` | `name` | Bring window to foreground |
| `list` | — | List all running applications |

## Ask Claude

> "Open TextEdit"

> "Open Spotify"

> "Close Safari"

> "Switch to Finder"

> "What apps are currently running?"

---

## SDK examples

```python
params = AppTool.Params

await tool.execute(ctx, params(action="open", name="TextEdit"))
await tool.execute(ctx, params(action="focus", name="Safari"))
result = await tool.execute(ctx, params(action="list"))
print(result.output)
await tool.execute(ctx, params(action="close", name="TextEdit"))
```

---

Next: [clipboard →](clipboard.md)
