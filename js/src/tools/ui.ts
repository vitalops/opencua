import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { Tool, ToolContext, ToolResult, checkPermission } from "./base.js";
import { getSandbox } from "../computer/sandbox.js";

const exec = promisify(execFile);

export class UITool extends Tool {
  name = "ui";
  description =
    "Interact with UI elements by their accessible label — the primary interaction tool. " +
    "No pixel coordinates needed. Use this before the mouse tool.";

  schema = {
    type: "object",
    required: ["action", "app"],
    properties: {
      action: { type: "string", enum: ["get_tree", "click", "click_menu", "type", "press_key", "get_value"] },
      app: { type: "string", description: "Target application name" },
      title: { type: "string", description: "Element label — for click/get_value" },
      role: { type: "string", description: "Element role — for click/get_value" },
      menu: { type: "string", description: "Menu bar label — for click_menu" },
      menuItem: { type: "string", description: "Menu item label — for click_menu" },
      text: { type: "string", description: "Text to type — for type" },
      key: { type: "string", description: "Key name — for press_key" },
      modifiers: { type: "array", items: { type: "string" }, description: "Modifier keys — for press_key" },
    },
  };

  async execute(ctx: ToolContext, params: Record<string, unknown>): Promise<ToolResult> {
    const { action, app, title, role, menu, menuItem, text, key, modifiers = [] } = params as {
      action: string; app: string; title?: string; role?: string; menu?: string;
      menuItem?: string; text?: string; key?: string; modifiers?: string[];
    };

    await checkPermission(ctx, "ui", `${action} in ${app}`, `UI ${action} in ${app}`);

    const platform = process.platform;

    try {
      if (platform === "darwin") {
        return await this.executeMac({ action, app, title, role, menu, menuItem, text, key, modifiers }, ctx);
      } else if (platform === "win32") {
        return await this.executeWin({ action, app, title, text, key }, ctx);
      } else {
        return await this.executeLinux({ action, app, title, text, key }, ctx);
      }
    } catch (e) {
      return this.err("UI error", `${e}`);
    }
  }

  private async executeMac(
    p: { action: string; app: string; title?: string; role?: string; menu?: string; menuItem?: string; text?: string; key?: string; modifiers?: string[] },
    ctx: ToolContext,
  ): Promise<ToolResult> {
    const sandbox = getSandbox(ctx.sessionId);

    switch (p.action) {
      case "get_tree": {
        const script = `
          tell application "System Events"
            tell process "${p.app}"
              set allElements to every UI element of window 1
              set result to {}
              repeat with el in allElements
                try
                  set result to result & {name of el & " [" & role of el & "]"}
                end try
              end repeat
              return result
            end tell
          end tell`;
        const { stdout } = await exec("osascript", ["-e", script]);
        sandbox.recordAction("ui_get_tree", { app: p.app }, "ok");
        return this.ok("UI tree", stdout.trim());
      }
      case "click": {
        const by = p.title ? `whose name is "${p.title}"` : p.role ? `whose role is "${p.role}"` : "";
        const script = `
          tell application "System Events"
            tell process "${p.app}"
              click (first UI element of window 1 ${by})
            end tell
          end tell`;
        await exec("osascript", ["-e", script]);
        sandbox.recordAction("ui_click", { app: p.app, title: p.title }, "ok", undefined,
          { action: "click", app: p.app, title: p.title, role: p.role });
        return this.ok("UI click", `Clicked '${p.title ?? p.role}' in ${p.app}.`);
      }
      case "click_menu": {
        const script = `
          tell application "System Events"
            tell process "${p.app}"
              click menu item "${p.menuItem}" of menu "${p.menu}" of menu bar 1
            end tell
          end tell`;
        await exec("osascript", ["-e", script]);
        sandbox.recordAction("ui_click_menu", { app: p.app, menu: p.menu, menuItem: p.menuItem }, "ok", undefined,
          { action: "click_menu", app: p.app, menu: p.menu, menuItem: p.menuItem });
        return this.ok("UI click menu", `Clicked ${p.app} > ${p.menu} > ${p.menuItem}.`);
      }
      case "type": {
        const script = `
          tell application "System Events"
            tell process "${p.app}"
              keystroke "${p.text?.replace(/"/g, '\\"')}"
            end tell
          end tell`;
        await exec("osascript", ["-e", script]);
        sandbox.recordAction("ui_type", { app: p.app }, "ok", undefined,
          { action: "type", app: p.app, text: p.text });
        return this.ok("UI type", `Typed text in ${p.app}.`);
      }
      case "press_key": {
        const modMap: Record<string, string> = { command: "command down", shift: "shift down", option: "option down", control: "control down" };
        const using = (p.modifiers ?? []).map((m) => modMap[m] ?? m).join(", ");
        const usingClause = using ? ` using {${using}}` : "";
        const script = `
          tell application "System Events"
            tell process "${p.app}"
              keystroke "${p.key}"${usingClause}
            end tell
          end tell`;
        await exec("osascript", ["-e", script]);
        sandbox.recordAction("ui_press_key", { app: p.app, key: p.key }, "ok", undefined,
          { action: "press_key", app: p.app, key: p.key, modifiers: p.modifiers ?? [] });
        return this.ok("UI press key", `Pressed ${p.key} in ${p.app}.`);
      }
      case "get_value": {
        const by = p.title ? `whose name is "${p.title}"` : p.role ? `whose role is "${p.role}"` : "";
        const script = `
          tell application "System Events"
            tell process "${p.app}"
              get value of (first UI element of window 1 ${by})
            end tell
          end tell`;
        const { stdout } = await exec("osascript", ["-e", script]);
        sandbox.recordAction("ui_get_value", { app: p.app, title: p.title }, stdout.trim());
        return this.ok("UI get value", stdout.trim());
      }
      default:
        return this.err("UI error", `Unknown action: ${p.action}`);
    }
  }

  private async executeWin(
    p: { action: string; app: string; title?: string; text?: string; key?: string },
    ctx: ToolContext,
  ): Promise<ToolResult> {
    const script = (ps: string) => exec("powershell", ["-Command", ps]);
    const sandbox = getSandbox(ctx.sessionId);

    switch (p.action) {
      case "click": {
        const ps = `
          Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
          $desktop = [System.Windows.Automation.AutomationElement]::RootElement
          $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, "${p.title}")
          $el = $desktop.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
          $invokePattern = $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
          $invokePattern.Invoke()`;
        await script(ps);
        sandbox.recordAction("ui_click", { app: p.app, title: p.title }, "ok", undefined,
          { action: "click", app: p.app, title: p.title });
        return this.ok("UI click", `Clicked '${p.title}' in ${p.app}.`);
      }
      case "type": {
        const ps = `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('${p.text}')`;
        await script(ps);
        sandbox.recordAction("ui_type", { app: p.app }, "ok", undefined,
          { action: "type", app: p.app, text: p.text });
        return this.ok("UI type", `Typed text in ${p.app}.`);
      }
      default:
        return this.err("UI error", `Action '${p.action}' not yet supported on Windows.`);
    }
  }

  private async executeLinux(
    p: { action: string; app: string; title?: string; text?: string; key?: string },
    ctx: ToolContext,
  ): Promise<ToolResult> {
    const sandbox = getSandbox(ctx.sessionId);

    switch (p.action) {
      case "click": {
        await exec("xdotool", ["search", "--name", p.title ?? p.app, "click", "1"]);
        sandbox.recordAction("ui_click", { app: p.app, title: p.title }, "ok", undefined,
          { action: "click", app: p.app, title: p.title });
        return this.ok("UI click", `Clicked '${p.title}' in ${p.app}.`);
      }
      case "type": {
        await exec("xdotool", ["type", "--clearmodifiers", p.text ?? ""]);
        sandbox.recordAction("ui_type", { app: p.app }, "ok", undefined,
          { action: "type", app: p.app, text: p.text });
        return this.ok("UI type", `Typed text in ${p.app}.`);
      }
      default:
        return this.err("UI error", `Action '${p.action}' not yet supported on Linux.`);
    }
  }
}
