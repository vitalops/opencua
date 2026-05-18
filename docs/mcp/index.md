# MCP Integration

MCP (Model Context Protocol) is how AI coding tools like Claude Code, Cursor, and Continue
connect to external capabilities. opendesk ships as an MCP server — you install it once, register
it with your client, and from then on any conversation can use screenshot, mouse, keyboard, ui,
app, clipboard, and ocr without any extra setup.

---

## How it works

```
Your AI client (Claude Code / Cursor / Continue)
       |
       | MCP over stdio
       v
  opendesk-mcp  <-- the server this package installs
       |
       +-- screenshot tool  (captures screen, returns PNG to the LLM)
       +-- ui tool          (clicks elements by name via AX tree)
       +-- mouse tool       (pixel-level mouse control with Retina scaling)
       +-- keyboard tool    (types text, presses keys, hotkeys)
       +-- app tool         (opens, closes, focuses apps)
       +-- clipboard tool   (read/write clipboard)
       +-- ocr tool         (extracts text from any screen region)
```

The LLM decides when to call a tool, calls it, gets the result (including PNG screenshots),
and continues. You never write tool-calling code yourself.

---

## What the LLM sees

Once registered, the LLM sees these tools in every conversation:

| Tool | When the LLM uses it |
|------|---------------------|
| `screenshot` | "Let me see what's on the screen" |
| `ui` | "Click the Save button" (finds it via AX tree, no coordinates) |
| `mouse` | Last resort for canvas areas with no accessible elements |
| `keyboard` | "Type this text into the field" |
| `app` | "Open Terminal" / "List running apps" |
| `clipboard` | "Copy this to clipboard" / "What did I copy?" |
| `ocr` | "Read the text in that region" |

The LLM follows the priority rule automatically because it's in each tool's description:
`ui` first, then `screenshot(marks=True)` to see numbered elements, then `mouse` as last resort.

---

## Setup

1. [Install](install.md) the package
2. Register with your client: [Claude Code](claude-code.md) · [Claude Desktop](claude-desktop.md) · [Cursor](cursor.md) · [Continue](continue.md)
