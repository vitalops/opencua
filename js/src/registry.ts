import { Tool } from "./tools/base.js";
import { ScreenshotTool } from "./tools/screenshot.js";
import { MouseTool } from "./tools/mouse.js";
import { KeyboardTool } from "./tools/keyboard.js";
import { AppTool } from "./tools/app.js";
import { ClipboardTool } from "./tools/clipboard.js";
import { OCRTool } from "./tools/ocr.js";
import { UITool } from "./tools/ui.js";
import { AuditTool } from "./tools/audit.js";
import { MemoryTool } from "./tools/memory.js";

export class ToolRegistry {
  private tools = new Map<string, Tool>();

  register(tool: Tool): this {
    this.tools.set(tool.name, tool);
    return this;
  }

  get(name: string): Tool {
    const tool = this.tools.get(name);
    if (!tool) throw new Error(`Tool '${name}' not found. Available: ${[...this.tools.keys()].join(", ")}`);
    return tool;
  }

  all(): Tool[] {
    return [...this.tools.values()];
  }
}

export function createRegistry(): ToolRegistry {
  const registry = new ToolRegistry();
  for (const tool of [
    new ScreenshotTool(),
    new MouseTool(),
    new KeyboardTool(),
    new AppTool(),
    new UITool(),
    new ClipboardTool(),
    new OCRTool(),
    new AuditTool(),
    new MemoryTool(),
  ]) {
    registry.register(tool);
  }
  return registry;
}
