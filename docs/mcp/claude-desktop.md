# Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "opendesk": {
      "command": "opendesk-mcp"
    }
  }
}
```

Restart Claude Desktop. The tools appear in the toolbar.

If `opendesk-mcp` is not on your PATH (e.g. in a virtualenv), use the full path:

```json
{
  "mcpServers": {
    "opendesk": {
      "command": "/path/to/venv/bin/opendesk-mcp"
    }
  }
}
```

---

Next up: [Cursor →](cursor.md)
