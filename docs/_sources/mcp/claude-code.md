# Claude Code

## Python

```bash
claude mcp add opendesk -- opendesk-mcp
```

Verify it was added:

```bash
claude mcp list
# opendesk: opendesk-mcp
```

To remove it later:

```bash
claude mcp remove opendesk
```

## JavaScript / TypeScript

```bash
npm install @vitalops/opendesk-sdk
npx opendesk-js install
```

To remove:

```bash
npx opendesk-js uninstall
```

---

Now start a conversation in Claude Code and ask:
> "Take a screenshot and tell me what's on my screen."

Claude will call the `screenshot` tool, receive the PNG, and describe it.

---

Next up: [Claude Desktop →](claude-desktop.md)
