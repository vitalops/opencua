# Troubleshooting

**`opendesk-js discover` shows nothing.**

The controlled machine must be running `opendesk-js pair` or `opendesk-js serve` with `--no-mdns` *not* set.
mDNS is multicast UDP — it doesn't cross routers, only the same LAN segment.
Some Wi-Fi access points isolate clients ("AP isolation") — check the AP settings.

---

**`opendesk-js pair-with` says "wrong_code".**

Make sure the code on the controlled machine matches what you typed. Codes change every `opendesk-js pair` run.

---

**`opendesk-js serve` says "no trusted peers yet".**

You haven't paired anything. Run `opendesk-js pair` first; once one peer pairs, `serve` will start accepting connections.

---

**The agent gets "Multiple peers paired … and no default set".**

That's deliberate. Run `opendesk_use <name>` from the agent (or pass `peer: <name>` on each tool call) so opendesk knows where actions should land.

---

**Permissions on macOS.**

`opendesk-js serve` needs Accessibility + Screen Recording permission to drive mouse/keyboard and capture the screen — same as the local CLI. Grant them to the `node` binary (or the terminal running it).

---

**Same machine testing (loopback).**

When running controller and controlled on the same machine, both share `~/.opendesk/trusted-peers.json`. Use `--home /tmp/ctrl` on one side to keep the identity stores separate:

```bash
opendesk-js pair --home /tmp/ctrl --port 9000
# In another terminal:
opendesk-js pair-with 127.0.0.1 XXXXXX --home /tmp/ctrl --port 9000
```

---

Looking for the Python/CLI equivalent? See [Remote — Python →](../remote/index.md)
