# Programmatic Use

## Connect to a paired peer

```typescript
import { connect } from "@vitalops/opendesk-sdk";

const remote = await connect("mini");         // looks up ~/.opendesk/trusted-peers.json
const shot   = await remote.capture();
console.log(shot.width, shot.height);
await remote.close();
```

`RemoteComputer` exposes the same surface as the local computer —
`capture()`, `cursor()`, `pointer()`, `key()`, `windows()`, `clipboard()`,
`uiTree()`, `shell()`, and more. Auto-reconnect with exponential back-off is
on by default.

## Connect via DiscoveredPeer or explicit URL

```typescript
import { discover, connect } from "@vitalops/opendesk-sdk";

// mDNS browse
const [peer] = await discover(2000);          // { name, host, port, publicKey, fingerprint }
const remote = await connect(peer);

// Explicit URL (no trusted-peers store required)
const remote2 = await connect("ws://192.168.1.42:8423#<pubkey-hex>");
```

## Pair programmatically

```typescript
import { pairWith } from "@vitalops/opendesk-sdk";

const { remote, serverPubkey } = await pairWith(
  "192.168.1.42", 8423, "428901",
  { name: "mini" },
);
// remote is a connected RemoteComputer; serverPubkey is a Buffer
await remote.close();
```

## Run the server daemon from Node.js

```typescript
import {
  OpendeskServer, Identity, TrustedPeers,
  ToolDispatcher, AuditLog,
  createRegistry, allowAllContext,
} from "@vitalops/opendesk-sdk";

const identity = Identity.loadOrCreate();       // ~/.opendesk/identity.key
const trusted  = new TrustedPeers();            // ~/.opendesk/trusted-peers.json
const registry = createRegistry();
const ctx      = allowAllContext();
const audit    = new AuditLog();                // ~/.opendesk/audit/

const server = new OpendeskServer(identity, trusted, {
  audit,
  dispatcherFactory: ({ peerName, peerFingerprint, sessionId }) =>
    new ToolDispatcher({ registry, ctx, audit, peerName, peerFingerprint, sessionId }),
});

await server.start();
console.log("Listening on port", server.port);
```

## Identity and peer storage

```typescript
import { Identity, TrustedPeers, fingerprint } from "@vitalops/opendesk-sdk";

const identity = Identity.loadOrCreate();
console.log(fingerprint(identity.publicBytes));   // e.g. 9c2f:1abc:b3d4:8870

const peers = new TrustedPeers();
console.log(peers.list());
peers.setDefault("mini");
```

---

Running into issues? See [Troubleshooting →](troubleshooting.md)
