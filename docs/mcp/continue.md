# Continue (VS Code)

Edit `.continue/config.json`:

## Python

```json
{
  "mcpServers": [
    {
      "name": "opendesk",
      "command": "opendesk-mcp",
      "transport": "stdio"
    }
  ]
}
```

## JavaScript / TypeScript

```json
{
  "mcpServers": [
    {
      "name": "opendesk",
      "command": "node",
      "args": ["/path/to/node_modules/@vitalops/opendesk-sdk/bin/opendesk-mcp.js"],
      "transport": "stdio"
    }
  ]
}
```

---

## Any other MCP client

The pattern is always the same — point the client at the `opendesk-mcp` command with stdio transport.
opendesk follows the MCP spec exactly, so it works with any compliant client.

---

Need a custom permission policy or want to embed the server in your own process? See [Advanced →](advanced.md)
