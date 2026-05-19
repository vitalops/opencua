# `learn` — Record and Replay Tasks

```python
from opendesk.tools.learn import LearnTool
tool = LearnTool()
```

Requires `pip install 'opendesk[learn]'` (installs `pynput`).

## Actions

| Action | Parameters | Description |
|--------|-----------|-------------|
| `start` | `task_name` | Begin recording mouse, keyboard, and screenshots globally |
| `stop` | — | Stop recording; returns trajectory summary and screenshots |
| `save` | `task_name`, `procedure` | Save a procedure JSON string to `.opendesk/learned/` |
| `replay` | `task_name` | Load a procedure and return step-by-step replay instructions |
| `list` | — | List all saved procedures in the current directory |

## Ask Claude

**Recording a workflow:**
> "Watch me fill out this form and remember it as 'expense-report'"

Claude will call `learn(start)`, wait for you to perform the task, then call `learn(stop)` and summarize what it saw.

**Replaying a saved workflow:**
> "Replay the expense-report task"
> "Run the fill-form procedure again"

**Listing saved workflows:**
> "What tasks have you learned?"
> "Show me all saved procedures"

!!! note
    `learn` + `replay` teaches Claude to re-execute a workflow using *current screen state* — it adapts to the environment rather than replaying raw coordinates. For exact action-by-action replay of a session, use [`audit(action='replay')`](audit.md) instead.

---

## SDK examples

```python
params = LearnTool.Params

# Start recording
await tool.execute(ctx, params(action="start", task_name="fill-form"))

# ... user performs the task ...

# Stop and review trajectory
result = await tool.execute(ctx, params(action="stop"))
print(result.output)

# Save procedure (JSON string)
import json
procedure = json.dumps({
    "task_name": "fill-form",
    "description": "Fill and submit the expense form",
    "steps": ["Open the form", "Fill in fields", "Click Submit"],
    "procedure": "Navigate to the form application. Fill each required field. Submit."
})
await tool.execute(ctx, params(action="save", task_name="fill-form", procedure=procedure))

# Replay
result = await tool.execute(ctx, params(action="replay", task_name="fill-form"))
print(result.output)  # step-by-step instructions for the agent

# List all
result = await tool.execute(ctx, params(action="list"))
print(result.output)
```

See [Automation](../automation/index.md) for the full guide including scheduling, storage format, and platform support.
