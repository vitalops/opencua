# Adding a Custom Tool

```python
from opendesk.tools.base import Tool, ToolContext, ToolResult
from opendesk.registry import create_registry
from pydantic import Field

class PingTool(Tool):
    name = "ping"
    description = "Check if a host is reachable from the active computer."

    class Params(Tool.Params):
        host: str = Field(description="Hostname or IP to ping.")
        count: int = Field(default=3, description="Number of pings.")

    async def execute(self, ctx: ToolContext, params: "PingTool.Params") -> ToolResult:
        result = await ctx.computer.exec(["ping", "-c", str(params.count), params.host])
        return ToolResult(
            title=f"Ping {params.host}",
            output=result.stdout_text(),
            error=result.returncode != 0,
        )

# Register and use
registry = create_registry()
registry.register(PingTool())
```

Because the tool calls `ctx.computer.exec(...)`, it runs on the local
machine when `ctx.computer = LocalComputer`, on a remote peer when
`ctx.computer = RemoteComputer` — same code, both paths. And because the
schema comes from Pydantic, it automatically appears in MCP, Anthropic,
OpenAI, and LangChain adapters with no extra adapter code.

---

That covers the architecture. For the low-level wire format, see the [Protocol reference →](../protocol/index.md)
