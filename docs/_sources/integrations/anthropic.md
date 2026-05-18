# Anthropic SDK

## Agentic loop (recommended)

```python
import anthropic
from opendesk.integrations.claude_code import ClaudeCodeAdapter
from opendesk.registry import create_registry
from opendesk.tools.base import allow_all_context

client = anthropic.Anthropic()
adapter = ClaudeCodeAdapter(
    registry=create_registry(),
    ctx=allow_all_context(),
)

messages = [{"role": "user", "content": "Open TextEdit and type 'Hello World'"}]

result = await adapter.run_loop(
    client=client,
    model="claude-opus-4-6",
    messages=messages,
    system=(
        "You are a computer use agent. "
        "Always use the ui tool first. "
        "Use the mouse tool only for unlabelled canvas areas."
    ),
    max_tokens=8192,
    max_iterations=20,
)
print(result)
```

## Manual control

```python
response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=4096,
    tools=adapter.tool_definitions(),
    messages=messages,
)

messages.append({"role": "assistant", "content": response.content})

if response.stop_reason == "tool_use":
    # Executes all tool_use blocks in parallel
    tool_results = await adapter.handle_response(response)
    messages.append({"role": "user", "content": tool_results})
```

## Tool definitions format

```python
adapter.tool_definitions()
# Returns:
# [
#   {
#     "name": "screenshot",
#     "description": "...",
#     "input_schema": { "type": "object", "properties": {...} }
#   },
#   ...
# ]
```

---

Also using OpenAI-compatible APIs? Continue to [OpenAI →](openai.md)
