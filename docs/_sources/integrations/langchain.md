# LangChain / LangGraph

```python
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from opendesk.integrations.langchain_compat import as_langchain_tools
from opendesk.registry import create_registry

tools = as_langchain_tools(create_registry())
llm = ChatAnthropic(model="claude-opus-4-6")
agent = create_react_agent(llm, tools)

result = agent.invoke({"messages": [("user", "Open Safari and take a screenshot")]})
print(result["messages"][-1].content)
```

## With custom context

```python
from opendesk.tools.base import ToolContext, PermissionDeniedError

async def strict_policy(tool: str, argument: str, description: str) -> None:
    if tool == "app" and "open" in argument:
        raise PermissionDeniedError("App launching not allowed in this session.")

ctx = ToolContext(session_id="langchain", permission_handler=strict_policy)
tools = as_langchain_tools(create_registry(), ctx=ctx)
```

---

Working in Node.js? The [JavaScript SDK →](javascript.md) covers Vercel AI SDK, LangChain.js, and native MCP.
