# Troubleshooting

**`opendesk discover` shows nothing.**

The controlled machine must be running `opendesk pair` or `opendesk serve` with `--no-mdns` *not* set.
mDNS is multicast UDP — it doesn't cross routers, only the same LAN segment. Some Wi-Fi access
points isolate clients from each other ("AP isolation") — check the AP settings.

---

**`opendesk pair-with` says "wrong_code".**

Make sure the code on the controlled machine matches what you typed. Codes change every `opendesk pair` run.

---

**`opendesk serve` says "no trusted peers yet".**

You haven't paired anything. Run `opendesk pair` first; once one peer pairs, `serve` will start accepting connections.

---

**The agent gets `Multiple peers paired (...) and no default set`.**

That's deliberate. Run `opendesk_use <name>` from the agent (or pass `peer: <name>` on each call) so opendesk knows where actions should land.

---

**Permissions on macOS.**

`opendesk serve` needs Accessibility + Screen Recording permission to drive mouse/keyboard and capture the screen — same as the local CLI. Grant them to the Python / terminal binary running `opendesk serve`.

---

Using the JavaScript SDK instead? The [Remote JS/TS guide →](../remote-js/index.md) mirrors this flow with JS-native tooling.
