# opendesk documentation

**Open Computer Use Agent** — gives any AI agent eyes and hands on your desktop. Works on macOS, Linux, and Windows.

## Contents

| Doc | Description |
|-----|-------------|
| [Quickstart](quickstart.md) | Install, first screenshot, agentic loop |
| [MCP integration](mcp.md) | Add opendesk to Claude Code, Claude Desktop, Cursor, Continue |
| [Automation](automation.md) | Record, replay, and schedule desktop tasks |
| [Tools reference](tools.md) | Full parameter docs for every tool |
| [Integrations](integrations.md) | Anthropic SDK, OpenAI, LangChain |
| [Remote control](remote.md) | Pair machines, run the daemon, security model, CLI reference |
| [Architecture](architecture.md) | How the layers fit together, how to add custom tools |
| [Protocol](protocol.md) | Wire format, handshakes, frames, encryption, mDNS |

## Key concepts

### Tool priority

Always follow this order:

1. `ui` — click by element name via accessibility tree
2. `screenshot` — Set-of-Marks overlay
3. `mouse` — fallback for unlabelled canvas areas

### Set-of-Marks (SoM)

The screenshot tool draws numbered bounding boxes over every interactive element using data from the platform's native accessibility API (AppleScript on macOS, AT-SPI2 on Linux, UI Automation on Windows). The model can say "click mark 3" instead of guessing pixel coordinates.

### HiDPI scaling

On high-resolution displays (Retina, 4K), screenshot pixels ≠ logical pixels. Pass `image_width` and `image_height` from the screenshot result to the mouse tool and coordinates are translated automatically.

### Learn & replay

The `learn` tool records any desktop workflow (mouse, keyboard, screenshots) and summarizes it into a reusable procedure. Replay it later and the agent re-executes the steps using the current screen state — no hardcoded coordinates or paths.

### Permission model

Every action goes through `ToolContext.check_permission()` before execution. `allow_all_context()` approves everything; `interactive_context()` prompts in the terminal; or inject a custom async callable for your own policy engine.

### Sandbox

Each session has a `ComputerSandbox` that records a full audit log, enforces an app allow-list, and can restrict interactions to a screen region.
