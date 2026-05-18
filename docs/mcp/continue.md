# Continue (VS Code)

Edit `.continue/config.json`:

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

---

## Any other MCP client

The pattern is always the same — point the client at the `opendesk-mcp` command with stdio transport.
opendesk follows the MCP spec exactly, so it works with any compliant client.

---

Need a custom permission policy or want to embed the server in your own process? See [Advanced →](advanced.md)
