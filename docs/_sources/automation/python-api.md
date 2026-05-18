# Python API

## Learn & Replay

```python
from opendesk import create_registry, allow_all_context

registry = create_registry()
ctx = allow_all_context()
learn = registry.get("learn")

# Start recording
await learn.execute(ctx, learn.Params(action="start", task_name="expense-report"))

# ... user performs the task ...

# Stop — returns event log + screenshots
result = await learn.execute(ctx, learn.Params(action="stop"))
print(result.output)

# Save the procedure (JSON summarized by an LLM)
import json
procedure = json.dumps({
    "task_name": "expense-report",
    "description": "Fill and submit the monthly expense report",
    "steps": ["Open the form", "Fill in all fields", "Click Submit"],
    "procedure": "Navigate to the expense report tool. Fill each required field. Submit."
})
await learn.execute(ctx, learn.Params(action="save", task_name="expense-report", procedure=procedure))

# Replay
result = await learn.execute(ctx, learn.Params(action="replay", task_name="expense-report"))
print(result.output)  # step-by-step instructions for the agent

# List all
result = await learn.execute(ctx, learn.Params(action="list"))
print(result.output)
```

## Schedule

```python
schedule = registry.get("schedule")

# Schedule a learned procedure
await schedule.execute(ctx, schedule.Params(
    action="add",
    name="expense-report",
    task="replay expense-report",
    timing="every friday at 17:00",
))

# Schedule a natural language task (no prior recording needed)
await schedule.execute(ctx, schedule.Params(
    action="add",
    name="hourly-screenshot",
    task="take a screenshot and save it to /tmp/screenshots/",
    timing="every 1h",
))

# List schedules
result = await schedule.execute(ctx, schedule.Params(action="list"))
print(result.output)

# Run immediately (for testing)
result = await schedule.execute(ctx, schedule.Params(action="run", name="expense-report"))

# Remove
await schedule.execute(ctx, schedule.Params(action="remove", name="hourly-screenshot"))
```

---

Ready to run schedules continuously? The [Scheduler →](scheduler.md) covers the background daemon, timing formats, and storage.
