import { describe, it, expect, vi, beforeEach } from "vitest";
import { ComputerSandbox } from "../src/computer/sandbox.js";
import { AuditTool } from "../src/tools/audit.js";
import type { ToolContext } from "../src/tools/base.js";

function makeCtx(sessionId = "test-session"): ToolContext {
  return { sessionId, permissionHandler: undefined, metadata: {} } as ToolContext;
}

function freshSandbox(sessionId = "test-session"): ComputerSandbox {
  return new ComputerSandbox(sessionId);
}

// ---------------------------------------------------------------------------
// ComputerSandbox — replayParams storage
// ---------------------------------------------------------------------------

describe("ComputerSandbox.recordAction", () => {
  it("stores replayParams when provided", () => {
    const sb = freshSandbox();
    const entry = sb.recordAction(
      "keyboard_type",
      { action: "type" },
      "ok",
      undefined,
      { action: "type", text: "hello" },
    );
    expect(entry.replayParams).toEqual({ action: "type", text: "hello" });
  });

  it("leaves replayParams undefined when not provided", () => {
    const sb = freshSandbox();
    const entry = sb.recordAction("screenshot", {}, "ok");
    expect(entry.replayParams).toBeUndefined();
  });

  it("exportAuditLog includes replayParams in entries", () => {
    const sb = freshSandbox();
    sb.recordAction("mouse_click", { action: "click", x: 10, y: 20 }, "ok", undefined,
      { action: "click", x: 10, y: 20 });
    const log = sb.exportAuditLog();
    expect(log[0].replayParams).toEqual({ action: "click", x: 10, y: 20 });
  });

  it("appends multiple entries preserving order", () => {
    const sb = freshSandbox();
    sb.recordAction("keyboard_type", {}, "ok", undefined, { action: "type", text: "a" });
    sb.recordAction("keyboard_press", {}, "ok", undefined, { action: "press", key: "enter" });
    const log = sb.exportAuditLog();
    expect(log).toHaveLength(2);
    expect(log[0].action).toBe("keyboard_type");
    expect(log[1].action).toBe("keyboard_press");
  });
});

// ---------------------------------------------------------------------------
// AuditTool — show
// ---------------------------------------------------------------------------

describe("AuditTool show", () => {
  it("returns empty message when log is empty", async () => {
    const tool = new AuditTool();
    // Use a unique sessionId so no other test's sandbox leaks in
    const ctx = makeCtx("show-empty-" + Math.random());
    const result = await tool.execute(ctx, { action: "show", format: "full" });
    expect(result.output).toMatch(/No actions recorded/i);
  });

  it("summary returns one-line count", async () => {
    const sid = "show-summary-" + Math.random();
    // Populate via the sandbox map — getSandbox creates lazily
    const { getSandbox } = await import("../src/computer/sandbox.js");
    const sb = getSandbox(sid);
    sb.recordAction("keyboard_type", { action: "type" }, "ok");
    sb.recordAction("mouse_click", { action: "click" }, "ok");

    const tool = new AuditTool();
    const result = await tool.execute(makeCtx(sid), { action: "show", format: "summary" });
    expect(result.output).toMatch(/2 actions/);
  });

  it("full log marks replayable entries with ✓", async () => {
    const sid = "show-full-" + Math.random();
    const { getSandbox } = await import("../src/computer/sandbox.js");
    const sb = getSandbox(sid);
    sb.recordAction("keyboard_type", { action: "type" }, "ok", undefined,
      { action: "type", text: "hello" });
    sb.recordAction("screenshot", {}, "ok"); // not replayable

    const tool = new AuditTool();
    const result = await tool.execute(makeCtx(sid), { action: "show", format: "full" });
    expect(result.output).toContain("✓");
    expect(result.output).toContain("keyboard_type");
  });
});

// ---------------------------------------------------------------------------
// AuditTool — replay
// ---------------------------------------------------------------------------

describe("AuditTool replay", () => {
  it("returns empty message when log is empty", async () => {
    const tool = new AuditTool();
    const ctx = makeCtx("replay-empty-" + Math.random());
    const result = await tool.execute(ctx, { action: "replay" });
    expect(result.output).toMatch(/empty/i);
  });

  it("returns no-replayable message when all entries lack replay_params", async () => {
    const sid = "replay-noop-" + Math.random();
    const { getSandbox } = await import("../src/computer/sandbox.js");
    getSandbox(sid).recordAction("screenshot", {}, "ok");

    const tool = new AuditTool();
    const result = await tool.execute(makeCtx(sid), { action: "replay" });
    expect(result.output).toMatch(/No replayable/i);
  });

  it("skips errored entries when skipErrors=true (default)", async () => {
    const sid = "replay-skip-err-" + Math.random();
    const { getSandbox } = await import("../src/computer/sandbox.js");
    getSandbox(sid).recordAction(
      "keyboard_type", { action: "type" }, undefined, "some error",
      { action: "type", text: "hello" },
    );

    const tool = new AuditTool();
    const result = await tool.execute(makeCtx(sid), { action: "replay", skipErrors: true });
    expect(result.output).toMatch(/No replayable/i);
  });

  it("includes errored entries when skipErrors=false", async () => {
    const sid = "replay-include-err-" + Math.random();
    const { getSandbox } = await import("../src/computer/sandbox.js");
    getSandbox(sid).recordAction(
      "keyboard_type", { action: "type" }, undefined, "some error",
      { action: "type", text: "hello" },
    );

    // Mock the tool loader so no hardware is needed
    vi.doMock("../src/tools/keyboard.js", () => ({
      KeyboardTool: class {
        async execute() { return { error: false, output: "typed ok", title: "k", attachments: [], metadata: {} }; }
      },
    }));

    const tool = new AuditTool();
    const result = await tool.execute(makeCtx(sid), { action: "replay", skipErrors: false });
    // Should attempt replay (not "No replayable")
    expect(result.output).toMatch(/Replaying 1 action/);
  });

  it("dispatches to the correct tool and reports results", async () => {
    const sid = "replay-dispatch-" + Math.random();
    const { getSandbox } = await import("../src/computer/sandbox.js");
    getSandbox(sid).recordAction(
      "app_open", { action: "open", name: "Terminal" }, "ok", undefined,
      { action: "open", name: "Terminal" },
    );

    // Stub the app tool — avoid actual process spawning
    const mockExecute = vi.fn().mockResolvedValue({
      error: false, output: "Opened 'Terminal'.", title: "App open", attachments: [], metadata: {},
    });
    vi.doMock("../src/tools/app.js", () => ({
      AppTool: class { execute = mockExecute; },
    }));

    const tool = new AuditTool();
    const result = await tool.execute(makeCtx(sid), { action: "replay" });
    expect(result.output).toContain("1 succeeded");
    expect(result.output).toContain("app_open");
  });
});
