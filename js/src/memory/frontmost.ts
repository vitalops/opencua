/**
 * Frontmost application name and window title, per platform.  Used by the
 * screen-memory recorder for deny-list matching and for indexing.
 */

import { execFile } from "node:child_process";
import { promisify } from "node:util";

const exec = promisify(execFile);

export interface Frontmost {
  app: string;
  title: string;
}

async function run(cmd: string, args: string[], timeout = 5000): Promise<string> {
  try {
    const { stdout } = await exec(cmd, args, { timeout });
    return stdout.trim();
  } catch {
    return "";
  }
}

export async function frontmost(): Promise<Frontmost> {
  if (process.platform === "darwin") {
    const app = await run("osascript", [
      "-e", 'tell application "System Events" to get name of first process whose frontmost is true',
    ]);
    if (!app) return { app: "", title: "" };
    const title = await run("osascript", [
      "-e",
      'tell application "System Events" to tell (first process whose frontmost is true) to get name of front window',
    ]);
    return { app, title: title && title !== app ? title : "" };
  }
  if (process.platform === "linux") {
    const title = await run("xdotool", ["getactivewindow", "getwindowname"]);
    const app = await run("xdotool", ["getactivewindow", "getwindowclassname"]);
    return { app: app || title, title: app && title !== app ? title : "" };
  }
  if (process.platform === "win32") {
    const script = `
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class W { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid); }
"@
$h = [W]::GetForegroundWindow(); $sb = New-Object System.Text.StringBuilder 512
[void][W]::GetWindowText($h, $sb, 512); $pid2 = 0; [void][W]::GetWindowThreadProcessId($h, [ref]$pid2)
$p = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
Write-Output ($p.ProcessName); Write-Output ($sb.ToString())`;
    const out = await run("powershell", ["-NonInteractive", "-Command", script]);
    const [app = "", ...rest] = out.split(/\r?\n/);
    const title = rest.join(" ").trim();
    return { app: app.trim(), title: title !== app.trim() ? title : "" };
  }
  return { app: "", title: "" };
}
