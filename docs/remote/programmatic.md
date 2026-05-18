# Programmatic Use

## Python

```python
import asyncio
from opendesk.remote import connect

async def main():
    remote = await connect("mini")        # peer name from `opendesk peers list`
    try:
        pixmap = await remote.capture()
        print(pixmap.width, pixmap.height, len(pixmap.data))
    finally:
        await remote.aclose()

asyncio.run(main())
```

`remote` is a full `Computer` — drop it into any existing opendesk
`ToolContext` and every tool transparently targets the remote machine.

## JavaScript / TypeScript

Install the SDK:

```bash
npm install @vitalops/opendesk-sdk
```

Connect to a paired peer by name:

```typescript
import { connect } from "@vitalops/opendesk-sdk";

const remote = await connect("mini");         // looks up ~/.opendesk/trusted-peers.json
const shot   = await remote.capture();
console.log(shot.width, shot.height);
await remote.close();
```

Or connect to a `DiscoveredPeer` from `discover()`:

```typescript
import { discover, connect } from "@vitalops/opendesk-sdk";

const [peer] = await discover(2000);
const remote = await connect(peer);           // { host, port, publicKey }
```

Or by explicit URL (useful without a paired key store):

```typescript
const remote = await connect("ws://192.168.1.42:8423#<pubkey-hex>");
```

`RemoteComputer` exposes the same surface as the local computer — `capture()`,
`cursor()`, `pointer()`, `key()`, `windows()`, `clipboard()`, `uiTree()`,
`shell()`, and more. Auto-reconnect with exponential back-off is on by default.

### Serve from Node.js

```typescript
import { OpendeskServer, Identity, TrustedPeers } from "@vitalops/opendesk-sdk";
import { createRegistry, allowAllContext } from "@vitalops/opendesk-sdk";

const identity = Identity.loadOrCreate();           // ~/.opendesk/identity.key
const trusted  = new TrustedPeers();                // ~/.opendesk/trusted-peers.json
const registry = createRegistry();
const ctx      = allowAllContext();

const server = new OpendeskServer(identity, trusted, {
  dispatcherFactory: () => registry.makeDispatcher(ctx),
});
await server.start();
console.log("Listening on port", server.port);
```

### Pairing from Node.js

```typescript
import { pairWith } from "@vitalops/opendesk-sdk";

const { remote, serverPubkey } = await pairWith(
  "192.168.1.42", 8423, "428901",
  { name: "mini" },
);
// remote is a connected RemoteComputer; serverPubkey is a Buffer
```

### Identity and peer storage

Both Python and JS share `~/.opendesk/trusted-peers.json` (same snake_case keys),
so pairing done with the Python CLI is immediately usable from the JS SDK and vice versa.

```typescript
import { Identity, TrustedPeers, fingerprint } from "@vitalops/opendesk-sdk";

const identity = Identity.loadOrCreate();
console.log(fingerprint(identity.publicBytes));     // e.g. 9c2f:1abc:b3d4:8870

const peers = new TrustedPeers();
console.log(peers.list());
peers.setDefault("mini");
```

---

Running into issues? See [Troubleshooting →](troubleshooting.md)
