/**
 * OpenDeskClient — programmatic interface to all native opendesk tools.
 * No Python required. All desktop automation runs in Node.js.
 *
 * Usage:
 *
 *   import { OpenDeskClient } from "@vitalops/opendesk-sdk";
 *
 *   const client = new OpenDeskClient();
 *   const result = await client.screenshot({ marks: true });
 *   console.log(result.output);
 */

import { createRegistry, ToolRegistry } from "./registry.js";
import { allowAllContext, ToolContext, ToolResult, PermissionHandler } from "./tools/base.js";

export interface OpenDeskClientOptions {
  sessionId?: string;
  permissionHandler?: PermissionHandler;
  registry?: ToolRegistry;
}

export class OpenDeskClient {
  private registry: ToolRegistry;
  private ctx: ToolContext;

  constructor(options: OpenDeskClientOptions = {}) {
    this.registry = options.registry ?? createRegistry();
    this.ctx = {
      sessionId: options.sessionId ?? "default",
      permissionHandler: options.permissionHandler,
    };
  }

  private call(name: string, params: Record<string, unknown> = {}): Promise<ToolResult> {
    return this.registry.get(name).execute(this.ctx, params);
  }

  screenshot(params: { marks?: boolean; savePath?: string; region?: number[] } = {}): Promise<ToolResult> {
    return this.call("screenshot", params as Record<string, unknown>);
  }

  mouse(params: { action: string; x?: number; y?: number; endX?: number; endY?: number; direction?: string; amount?: number }): Promise<ToolResult> {
    return this.call("mouse", params as Record<string, unknown>);
  }

  keyboard(params: { action: string; text?: string; key?: string; keys?: string[]; holdDuration?: number }): Promise<ToolResult> {
    return this.call("keyboard", params as Record<string, unknown>);
  }

  app(params: { action: string; name?: string }): Promise<ToolResult> {
    return this.call("app", params as Record<string, unknown>);
  }

  ui(params: { action: string; app: string; title?: string; role?: string; menu?: string; menuItem?: string; text?: string; key?: string; modifiers?: string[] }): Promise<ToolResult> {
    return this.call("ui", params as Record<string, unknown>);
  }

  clipboard(params: { action: string; text?: string }): Promise<ToolResult> {
    return this.call("clipboard", params as Record<string, unknown>);
  }

  ocr(params: { region?: number[] } = {}): Promise<ToolResult> {
    return this.call("ocr", params as Record<string, unknown>);
  }

  audit(params: { format?: "summary" | "full"; sessionId?: string } = {}): Promise<ToolResult> {
    return this.call("audit", params as Record<string, unknown>);
  }

  /** Screen memory — search / show / timeline / status / pause / resume / deny / config / delete. */
  memory(params: {
    action: "search" | "show" | "timeline" | "status" | "pause" | "resume" | "deny" | "config" | "delete";
    query?: string; since?: string; until?: string; app?: string; limit?: number;
    id?: number; includeImage?: boolean; duration?: string;
    denyAdd?: string; denyRemove?: string;
    intervalSeconds?: number; storageCapMb?: number; retentionDays?: number; confirm?: boolean;
  }): Promise<ToolResult> {
    return this.call("memory", params as Record<string, unknown>);
  }

  listTools(): string[] {
    return this.registry.all().map((t) => t.name);
  }
}
