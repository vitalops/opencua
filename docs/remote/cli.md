# CLI Reference

All commands accept `--home DIR` to override the identity / trusted-peers
location (default `~/.opendesk`).

## Controlled machine

```
opendesk pair         [--port N] [--code XXXXXX] [--timeout S] [--no-mdns]
opendesk serve        [--port N] [--host H] [--no-mdns]
                      [--approve {auto,console}] [--no-audit] [--log-file PATH]
opendesk sessions                    # show the active controller (0 or 1)
opendesk disconnect                  # kick the active controller
opendesk unpair NAME                 # revoke trust + disconnect if active
opendesk check        [--open]       # macOS Accessibility / Screen Recording probe
opendesk audit        [--date Y-M-D] [--peer NAME] [--limit N] [--follow]
opendesk install-service / uninstall-service
```

## Controller

```
opendesk discover     [--timeout S]
opendesk pair-with    HOST CODE [--port N] [--name NAME]
opendesk connect      [PEER]         # PEER optional if a default is set
opendesk peers        [list | default [NAME|--clear] | rename NAME NEW | remove NAME]
```

---

Want `opendesk serve` to survive reboots? See [Service Install →](service.md)
