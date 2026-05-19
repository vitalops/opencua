import { Tool, ToolContext, ToolResult } from "./base.js";
import { getSandbox } from "../computer/sandbox.js";

// Actions that produce no side-effects — skip during replay.
const SKIP_REPLAY = new Set([
  "cursor_position", "screenshot", "ocr", "clipboard_read",
  "ui_get_tree", "ui_get_value", "app_list",
]);

// Maps audit action name → tool name for dispatch.
const ACTION_TO_TOOL: Record<string, string> = {
  keyboard_type: "keyboard", keyboard_press: "keyboard",
  keyboard_hotkey: "keyboard", keyboard_hold: "keyboard",
  mouse_click: "mouse", mouse_move: "mouse", mouse_double_click: "mouse",
  mouse_right_click: "mouse", mouse_scroll: "mouse", mouse_drag: "mouse",
  app_open: "app", app_close: "app", app_focus: "app",
  clipboard_write: "clipboard",
  ui_click: "ui", ui_click_menu: "ui", ui_type: "ui", ui_press_key: "ui",
};

async function loadTool(name: string): Promise<Tool> {
  switch (name) {
    case "keyboard": return new (await import("./keyboard.js")).KeyboardTool();
    case "mouse":    return new (await import("./mouse.js")).MouseTool();
    case "app":      return new (await import("./app.js")).AppTool();
    case "clipboard":return new (await import("./clipboard.js")).ClipboardTool();
    case "ui":       return new (await import("./ui.js")).UITool();
    default: throw new Error(`Unknown tool: ${name}`);
  }
}

export class AuditTool extends Tool {
  name = "audit";
  description =
    "Show or replay the session audit log.\n" +
    "  action='show'   — display recorded actions (format='summary' or 'full')\n" +
    "  action='replay' — re-execute every action from this session " +
    "(skips screenshots, reads, and optionally failed actions)";

  schema = {
    type: "object",
    properties: {
      action: { type: "string", enum: ["show", "replay"], default: "show" },
      format: { type: "string", enum: ["summary", "full"], default: "full",
                description: "For action=show: summary or full timestamped log" },
      sessionId: { type: "string", description: "Session to inspect — defaults to current session" },
      skipErrors: { type: "boolean", default: true,
                    description: "For action=replay: skip entries that originally errored" },
    },
  };

  async execute(ctx: ToolContext, params: Record<string, unknown>): Promise<ToolResult> {
    const action = (params.action as string) ?? "show";
    const sessionId = (params.sessionId as string) ?? ctx.sessionId;
    const sandbox = getSandbox(sessionId);

    if (action === "replay") {
      const skipErrors = (params.skipErrors as boolean) ?? true;
      return this._replay(ctx, sandbox, skipErrors);
    }

    // --- show ---
    const format = (params.format as string) ?? "full";
    if (format === "summary") {
      return this.ok("Audit summary", sandbox.summary());
    }

    const log = sandbox.exportAuditLog();
    if (!log.length) {
      return this.ok("Audit log", `No actions recorded yet for session '${sessionId}'.`);
    }

    const lines = [`Audit log — session '${sessionId}' (${log.length} actions)\n`];
    for (let i = 0; i < log.length; i++) {
      const entry = log[i];
      const ts = new Date(entry.timestamp * 1000).toISOString().replace("T", " ").slice(0, 19);
      const errorTag = entry.error ? `  ERROR: ${entry.error}` : "";
      const replayable = entry.replayParams || ACTION_TO_TOOL[entry.action] ? "✓" : " ";
      lines.push(
        `[${String(i + 1).padStart(3)}] [${replayable}] [${ts}] ${entry.action.padEnd(20)} ${JSON.stringify(entry.params)}${errorTag}`
      );
    }
    lines.push("\n✓ = replayable via audit(action='replay')");
    return this.ok("Audit log", lines.join("\n"));
  }

  private async _replay(
    ctx: ToolContext,
    sandbox: ReturnType<typeof getSandbox>,
    skipErrors: boolean,
  ): Promise<ToolResult> {
    const log = sandbox.exportAuditLog();
    if (!log.length) {
      return this.ok("Audit replay", "No actions to replay — the audit log is empty.");
    }

    const replayable = log.filter(
      (e) =>
        !SKIP_REPLAY.has(e.action) &&
        ACTION_TO_TOOL[e.action] !== undefined &&
        !(skipErrors && e.error),
    );

    if (!replayable.length) {
      return this.ok("Audit replay", "No replayable actions found in this session.");
    }

    const lines = [`Replaying ${replayable.length} action(s)...\n`];
    let ok = 0;
    let failed = 0;

    for (const entry of replayable) {
      const toolName = ACTION_TO_TOOL[entry.action];
      // Use replayParams if present (ui actions), otherwise params is already full (keyboard/mouse/etc.)
      const replayParams = entry.replayParams ?? entry.params;

      try {
        const tool = await loadTool(toolName);
        const result = await tool.execute(ctx, replayParams);
        const status = result.error ? "ERROR" : "OK";
        lines.push(`  ${status.padEnd(5)} ${entry.action.padEnd(20)} ${result.output.slice(0, 80)}`);
        if (result.error) failed++; else ok++;
      } catch (e) {
        lines.push(`  ERROR ${entry.action.padEnd(20)} ${e}`);
        failed++;
      }
    }

    lines.push(`\nDone — ${ok} succeeded, ${failed} failed.`);
    return this.ok("Audit replay", lines.join("\n"));
  }
}
