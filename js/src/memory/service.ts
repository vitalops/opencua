/**
 * Install / uninstall the screen-memory daemon as a user-scoped service —
 * mirrors Python's opendesk/memory/service.py.
 *
 * - Linux   systemd --user unit  ~/.config/systemd/user/opendesk-js-memory.service
 * - macOS   launchd agent        ~/Library/LaunchAgents/com.opendesk.js.memory.plist
 * - Windows Task Scheduler task  opendesk-js-memory (on logon)
 *
 * The `render*` functions are pure and unit-tested.
 */

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { memoryDir } from "./config.js";

export const SERVICE_NAME = "opendesk-js-memory";
export const LAUNCHD_LABEL = "com.opendesk.js.memory";

export interface ServiceInstallation {
  path: string;
  started: boolean;
  manager: "systemd" | "launchd" | "schtasks";
}

export interface ServiceOptions {
  home?: string;
  interval?: number;
  /** Path to the `opendesk-js` CLI entry script; defaults to this package's bin. */
  cliScript?: string;
  node?: string;
  autostart?: boolean;
}

function defaultCliScript(): string {
  // dist/memory/service.js → ../../bin/opendesk.js  (same for src/ under tsx)
  const here = path.dirname(new URL(import.meta.url).pathname);
  return path.resolve(here, "..", "..", "bin", "opendesk.js");
}

export function serviceArgs(node: string, cli: string, interval?: number, home?: string): string[] {
  const args = [node, cli, "memory", "start"];
  if (interval) args.push("--interval", String(interval));
  if (home) args.push("--home", home);
  return args;
}

function xml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function quote(s: string): string {
  return /[\s"]/.test(s) ? `"${s.replace(/"/g, '\\"')}"` : s;
}

// -- renderers -----------------------------------------------------------------

export function renderSystemdUnit(node: string, cli: string, interval?: number, home?: string): string {
  const cmd = serviceArgs(node, cli, interval, home).map(quote).join(" ");
  return `[Unit]
Description=opendesk screen memory (js) — local searchable desktop history
After=graphical-session.target

[Service]
Type=simple
ExecStart=${cmd}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
`;
}

export function renderLaunchdPlist(node: string, cli: string, interval?: number, home?: string): string {
  const logDir = memoryDirNoCreate(home);
  const args = serviceArgs(node, cli, interval, home).map((a) => `<string>${xml(a)}</string>`).join("\n        ");
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        ${args}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>${xml(path.join(logDir, "daemon.log"))}</string>
    <key>StandardErrorPath</key>
    <string>${xml(path.join(logDir, "daemon.err"))}</string>
</dict>
</plist>
`;
}

export function renderSchtasksCommand(node: string, cli: string, interval?: number, home?: string): string {
  return serviceArgs(node, cli, interval, home).map((a) => (/\s/.test(a) || a === node || a === cli ? `"${a}"` : a)).join(" ");
}

function memoryDirNoCreate(home?: string): string {
  const base = home ? path.resolve(home) : path.join(os.homedir(), ".opendesk");
  return path.join(base, "memory");
}

// -- install / uninstall -----------------------------------------------------------

function run(cmd: string, args: string[]): { ok: boolean } {
  try {
    execFileSync(cmd, args, { stdio: "ignore", timeout: 30_000 });
    return { ok: true };
  } catch {
    return { ok: false };
  }
}

function systemdUnitPath(): string {
  return path.join(os.homedir(), ".config", "systemd", "user", `${SERVICE_NAME}.service`);
}

function launchdPlistPath(): string {
  return path.join(os.homedir(), "Library", "LaunchAgents", `${LAUNCHD_LABEL}.plist`);
}

export function installMemoryService(opts: ServiceOptions = {}): ServiceInstallation {
  const node = opts.node ?? process.execPath;
  const cli = opts.cliScript ?? defaultCliScript();
  const autostart = opts.autostart ?? true;
  memoryDir(opts.home); // ensure log dir exists

  if (process.platform === "linux") {
    const p = systemdUnitPath();
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, renderSystemdUnit(node, cli, opts.interval, opts.home));
    let started = false;
    if (autostart) {
      run("systemctl", ["--user", "daemon-reload"]);
      started = run("systemctl", ["--user", "enable", "--now", SERVICE_NAME]).ok;
    }
    return { path: p, started, manager: "systemd" };
  }
  if (process.platform === "darwin") {
    const p = launchdPlistPath();
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, renderLaunchdPlist(node, cli, opts.interval, opts.home));
    let started = false;
    if (autostart) {
      run("launchctl", ["unload", p]);
      started = run("launchctl", ["load", "-w", p]).ok;
    }
    return { path: p, started, manager: "launchd" };
  }
  if (process.platform === "win32") {
    const tr = renderSchtasksCommand(node, cli, opts.interval, opts.home);
    const r = run("schtasks", ["/create", "/tn", SERVICE_NAME, "/tr", tr, "/sc", "onlogon", "/rl", "limited", "/f"]);
    if (!r.ok) throw new Error("schtasks /create failed");
    let started = false;
    if (autostart) started = run("schtasks", ["/run", "/tn", SERVICE_NAME]).ok;
    return { path: `TaskScheduler:${SERVICE_NAME}`, started, manager: "schtasks" };
  }
  throw new Error(`Service install not supported on platform: ${process.platform}`);
}

export function uninstallMemoryService(): boolean {
  if (process.platform === "linux") {
    const p = systemdUnitPath();
    if (!fs.existsSync(p)) return false;
    run("systemctl", ["--user", "disable", "--now", SERVICE_NAME]);
    fs.rmSync(p, { force: true });
    return true;
  }
  if (process.platform === "darwin") {
    const p = launchdPlistPath();
    if (!fs.existsSync(p)) return false;
    run("launchctl", ["unload", "-w", p]);
    fs.rmSync(p, { force: true });
    return true;
  }
  if (process.platform === "win32") {
    return run("schtasks", ["/delete", "/tn", SERVICE_NAME, "/f"]).ok;
  }
  throw new Error(`Service uninstall not supported on platform: ${process.platform}`);
}
