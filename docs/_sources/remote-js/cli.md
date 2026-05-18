# CLI Reference

All commands accept `--home DIR` to override the identity / trusted-peers
location (default `~/.opendesk`).

## Controlled machine

```
opendesk-js pair        [--port N] [--code XXXXXX] [--timeout S] [--no-mdns]
opendesk-js serve       [--port N] [--host H] [--no-mdns] [--approve {auto|console}]
opendesk-js sessions                       # show the active controller (0 or 1)
opendesk-js disconnect                     # cooperative eviction of active controller
opendesk-js unpair NAME                    # revoke trust (+ disconnect if active)
opendesk-js describe [TEXT] [--clear]      # read/set/clear this machine's broadcast description
opendesk-js audit       [--date Y-M-D] [--peer NAME] [--limit N] [--follow]
```

## Controller

```
opendesk-js discover    [--timeout S]
opendesk-js pair-with   HOST CODE [--port N] [--name NAME]
opendesk-js connect     [PEER]
opendesk-js peers       [list]
opendesk-js peers       default [NAME | --clear]
opendesk-js peers       rename  NAME NEW-NAME
opendesk-js peers       remove  NAME
opendesk-js peers       describe NAME [TEXT] [--clear]
opendesk-js unpair      NAME
```

## Shared

```
opendesk-js install     [--scope {user|project}]
opendesk-js uninstall
opendesk-js mcp                            # run MCP server over stdio
```

---

Next: [Concurrency →](concurrency.md) — the single-controller policy and how to hand off between machines.
