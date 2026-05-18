# Service Install

Keep `opendesk serve` running across reboots:

```
opendesk install-service     # registers a systemd user unit (Linux),
                             # launchd agent (macOS), or scheduled task (Windows)
opendesk uninstall-service   # removes it
```

The service runs as your user, against `~/.opendesk`, on the default port.

---

Next: [Concurrency →](concurrency.md) — what happens when multiple controllers try to connect.
