# OpenAI Function Calling

Works with OpenAI API and any OpenAI-compatible endpoint (Groq, Together AI, Ollama, LiteLLM, vLLM, Fireworks, etc.).

## Agentic loop

```python
from openai import OpenAI
from opendesk.integrations.openai_compat import OpenAIAdapter
from opendesk.registry import create_registry

client = OpenAI()
adapter = OpenAIAdapter(create_registry())

messages = [{"role": "user", "content": "Take a screenshot and describe what you see"}]
result = await adapter.run_loop(client, model="gpt-4o", messages=messages)
```

## With Groq

```python
from groq import Groq
client = Groq()
result = await adapter.run_loop(client, model="llama-3.3-70b-versatile", messages=messages)
```

## With Ollama

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
result = await adapter.run_loop(client, model="qwen2.5:72b", messages=messages)
```

## Manual control

```python
from openai import OpenAI
from opendesk.integrations.openai_compat import OpenAIAdapter

client = OpenAI()
adapter = OpenAIAdapter()

response = client.chat.completions.create(
    model="gpt-4o",
    tools=adapter.tool_definitions(),
    messages=messages,
)

choice = response.choices[0]
messages.append(choice.message)

if choice.finish_reason == "tool_calls":
    tool_messages = await adapter.handle_response(choice.message)
    messages.extend(tool_messages)
```

---

Using LangChain or LangGraph? See [LangChain →](langchain.md)
