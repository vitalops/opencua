# Troubleshooting

**`opendesk-mcp` not found**

The command is registered as a script entry point. Make sure you installed with `pip install 'opendesk[core,mcp]'`
and that the install's `bin/` directory is on your PATH.

---

**Screenshot returns a permission error on macOS**

Go to System Settings → Privacy & Security → Screen Recording and add your terminal app (Terminal, iTerm2, etc.).

---

**Mouse/keyboard actions do nothing on macOS**

Go to System Settings → Privacy & Security → Accessibility and add your terminal app.

---

**Tools appear in Claude Code but calls fail with `ImportError`**

You installed opendesk but not the core extras. Run:

```bash
pip install 'opendesk[core]'
```

---

Everything working? Explore [Integrations →](../integrations/index.md) to wire opendesk into your preferred agent framework, or browse the [Tools reference →](../tools/index.md) for full parameter docs.
