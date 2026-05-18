# Cursor

Create or edit `.cursor/mcp.json` in your project root (or `~/.cursor/mcp.json` globally):

## Python

```json
{
  "mcpServers": {
    "opendesk": {
      "command": "opendesk-mcp",
      "transport": "stdio"
    }
  }
}
```

## JavaScript / TypeScript

```json
{
  "mcpServers": {
    "opendesk": {
      "command": "node",
      "args": ["/path/to/node_modules/@vitalops/opendesk-sdk/bin/opendesk-mcp.js"],
      "transport": "stdio"
    }
  }
}
```

---

Next up: [Continue (VS Code) →](continue.md)
