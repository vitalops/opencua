/**
 * Standalone MCP server — exposes all native opendesk tools over the
 * Model Context Protocol.  No Python required.
 *
 * Supports both local tools and remote-peer tools:
 *
 *   - Local tools (screenshot, mouse, etc.) target the machine running this process.
 *   - Remote tools: pass `peer` argument to any tool to target a paired remote machine.
 *   - Session admin tools: opendesk_peers, opendesk_discover, opendesk_use,
 *     opendesk_status, opendesk_capabilities, opendesk_disconnect.
 *
 * Usage (stdio, for Claude Code / Claude Desktop):
 *   opendesk-js-mcp
 *
 * Or programmatically:
 *   import { createMcpServer, runMcpStdio } from "@vitalops/opendesk-sdk";
 *   const server = createMcpServer();
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { ListToolsRequestSchema, CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { createRegistry, ToolRegistry } from "./registry.js";
import { allowAllContext, ToolContext } from "./tools/base.js";
import { TrustedPeers } from "./protocol/auth/storage.js";
import type { RemoteComputer } from "./computer/remote.js";

// ---------------------------------------------------------------------------
// Per-MCP-session peer tracking
// ---------------------------------------------------------------------------

const LOCAL = "local";

/** Tools that always run on this machine, never routed to a peer.  Screen
 *  memory is deliberately local-only: the history never leaves the machine
 *  it was recorded on. */
const LOCAL_ONLY_TOOLS = new Set(["memory"]);

interface SessionState {
  defaultPeer: string; // "local" or peer name
  remotes: Map<string, RemoteComputer>;
}

function makeSession(): SessionState {
  return { defaultPeer: LOCAL, remotes: new Map() };
}

// One state object shared across the whole server process (single-session stdio)
const globalState: SessionState = makeSession();

async function getRemote(peerName: string, home?: string): Promise<RemoteComputer> {
  if (globalState.remotes.has(peerName)) return globalState.remotes.get(peerName)!;
  const { connect } = await import("./remote/client.js");
  const remote = await connect(peerName, { home });
  globalState.remotes.set(peerName, remote);
  return remote;
}

// ---------------------------------------------------------------------------
// Admin tools (opendesk_*)
// ---------------------------------------------------------------------------

const ADMIN_TOOLS = [
  {
    name: "opendesk_peers",
    description: "List trusted peers this controller has paired with.",
    inputSchema: {
      type: "object",
      properties: { home: { type: "string", description: "Override ~/.opendesk directory" } },
    },
  },
  {
    name: "opendesk_discover",
    description: "Discover opendesk peers advertising on the local network.",
    inputSchema: {
      type: "object",
      properties: {
        timeout: { type: "number", description: "Browse duration in ms (default 2000)" },
      },
    },
  },
  {
    name: "opendesk_use",
    description: "Set the default peer for this MCP session.",
    inputSchema: {
      type: "object",
      required: ["peer"],
      properties: {
        peer: { type: "string", description: "Peer name or 'local'" },
        home: { type: "string" },
      },
    },
  },
  {
    name: "opendesk_status",
    description: "Show the current default peer for this session.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "opendesk_capabilities",
    description: "Show capabilities of a peer (local or remote).",
    inputSchema: {
      type: "object",
      properties: { peer: { type: "string" }, home: { type: "string" } },
    },
  },
  {
    name: "opendesk_disconnect",
    description: "Close the connection to a remote peer.",
    inputSchema: {
      type: "object",
      properties: { peer: { type: "string" }, home: { type: "string" } },
    },
  },
];

async function callAdminTool(
  name: string,
  args: Record<string, unknown>,
): Promise<string> {
  switch (name) {
    case "opendesk_peers": {
      const store = new TrustedPeers(args["home"] as string | undefined);
      const peers = store.list();
      if (!peers.length) return "No trusted peers.";
      const def = store.getDefault();
      return peers
        .map((p) => {
          const marker = p.name === def ? " [default]" : "";
          const ep = p.lastHost ? `${p.lastHost}:${p.lastPort}` : "(unknown)";
          const desc = p.descriptionOverride || p.description;
          return `${p.name}  fp=${p.publicKey.slice(0, 8)}…  ${ep}${desc ? "  " + desc.slice(0, 60) : ""}${marker}`;
        })
        .join("\n");
    }

    case "opendesk_discover": {
      const { discover } = await import("./remote/discovery.js");
      const timeout = (args["timeout"] as number) ?? 2000;
      const found = await discover(timeout);
      if (!found.length) return "No opendesk peers found on the LAN.";
      return found
        .map((p) => `${p.name}  ${p.host}:${p.port}  fp=${p.fingerprint}  ${p.description}`)
        .join("\n");
    }

    case "opendesk_use": {
      const peer = args["peer"] as string;
      globalState.defaultPeer = peer;
      if (peer !== LOCAL) {
        // Open connection eagerly so errors surface now
        await getRemote(peer, args["home"] as string | undefined);
      }
      return `Default peer set to: ${peer}`;
    }

    case "opendesk_status": {
      const cur = globalState.defaultPeer;
      const connected = [...globalState.remotes.keys()];
      return `Default peer: ${cur}\nConnected: ${connected.length ? connected.join(", ") : "none"}`;
    }

    case "opendesk_capabilities": {
      const peerName = (args["peer"] as string) ?? globalState.defaultPeer;
      if (peerName === LOCAL) return "Local machine (all capabilities).";
      const remote = await getRemote(peerName, args["home"] as string | undefined);
      const caps = remote.capabilities();
      return JSON.stringify(caps, null, 2);
    }

    case "opendesk_disconnect": {
      const peerName = (args["peer"] as string) ?? globalState.defaultPeer;
      if (peerName === LOCAL) return "Cannot disconnect local machine.";
      const remote = globalState.remotes.get(peerName);
      if (!remote) return `No active connection to '${peerName}'.`;
      await remote.close();
      globalState.remotes.delete(peerName);
      if (globalState.defaultPeer === peerName) globalState.defaultPeer = LOCAL;
      return `Disconnected from '${peerName}'.`;
    }

    default:
      return `Unknown admin tool: ${name}`;
  }
}

// ---------------------------------------------------------------------------
// Main factory
// ---------------------------------------------------------------------------

export function createMcpServer(registry?: ToolRegistry, ctx?: ToolContext, home?: string): Server {
  const reg = registry ?? createRegistry();
  const context = ctx ?? allowAllContext();

  const server = new Server(
    { name: "opendesk", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => {
    const localTools = reg.all().map((t) => {
      if (LOCAL_ONLY_TOOLS.has(t.name)) {
        return { name: t.name, description: t.description, inputSchema: t.schema as Record<string, unknown> };
      }
      return {
        name: t.name,
        description: t.description + "\n\nPass `peer` (trusted peer name) to target a remote machine instead.",
        inputSchema: {
          ...(t.schema as Record<string, unknown>),
          properties: {
            ...((t.schema as Record<string, unknown>)["properties"] as Record<string, unknown> ?? {}),
            peer: { type: "string", description: "Target peer name (omit for local or session default)" },
          },
        },
      };
    });
    return { tools: [...localTools, ...ADMIN_TOOLS] };
  });

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args = {} } = request.params;
    const a = args as Record<string, unknown>;

    // Admin tools
    if (name.startsWith("opendesk_")) {
      const text = await callAdminTool(name, a);
      return { content: [{ type: "text", text }] };
    }

    // Determine peer
    const peerArg = a["peer"] as string | undefined;
    const effectivePeer = LOCAL_ONLY_TOOLS.has(name) ? LOCAL : (peerArg ?? globalState.defaultPeer);
    const { peer: _p, ...toolArgs } = a;
    void _p; // consumed

    let result;
    if (effectivePeer === LOCAL || effectivePeer === undefined) {
      const tool = reg.get(name);
      result = await tool.execute(context, toolArgs);
    } else {
      // Remote call: forward as a tool method call over the wire
      const remote = await getRemote(effectivePeer, home);
      const wireResult = await remote.call(`tool.${name}`, toolArgs);
      // Reconstruct a ToolResult-shaped object for the MCP response
      result = {
        output: String((wireResult?.["output"] as string) ?? JSON.stringify(wireResult)),
        error: Boolean(wireResult?.["error"]),
        attachments: (wireResult?.["attachments"] as unknown[]) ?? [],
      };
    }

    const contents: Array<{ type: string; text?: string; data?: string; mimeType?: string }> = [];
    if ((result as { output?: string }).output) {
      contents.push({ type: "text", text: (result as { output: string }).output });
    }
    for (const att of (result as { attachments?: Array<{ mediaType: string; content: Buffer; filename: string }> }).attachments ?? []) {
      if (att.mediaType.startsWith("image/")) {
        contents.push({ type: "image", data: att.content.toString("base64"), mimeType: att.mediaType });
      } else {
        contents.push({ type: "text", text: `[${att.filename}] data:${att.mediaType};base64,${att.content.toString("base64")}` });
      }
    }
    if (!contents.length) contents.push({ type: "text", text: "(no output)" });
    return { content: contents };
  });

  return server;
}

export async function runMcpStdio(registry?: ToolRegistry, ctx?: ToolContext, home?: string): Promise<void> {
  const server = createMcpServer(registry, ctx, home);
  const transport = new StdioServerTransport();
  await server.connect(transport);
}
