/**
 * Per-session audit sandbox — mirrors Python's computer/sandbox.py.
 */

export interface AuditEntry {
  id: string;
  timestamp: number;
  action: string;
  params: Record<string, unknown>;
  sessionId: string;
  result?: string;
  error?: string;
  replayParams?: Record<string, unknown>;
}

export class ComputerSandbox {
  readonly sessionId: string;
  allowedApps: string[] = [];
  screenRegion?: [number, number, number, number];
  lastScreenshot?: Buffer;
  private log: AuditEntry[] = [];

  constructor(sessionId: string) {
    this.sessionId = sessionId;
  }

  isAppAllowed(appName: string): boolean {
    if (this.allowedApps.length === 0) return true;
    return this.allowedApps.some((a) => appName.toLowerCase().includes(a.toLowerCase()));
  }

  isCoordinateAllowed(x: number, y: number): boolean {
    if (!this.screenRegion) return true;
    const [rx, ry, rw, rh] = this.screenRegion;
    return x >= rx && x <= rx + rw && y >= ry && y <= ry + rh;
  }

  recordAction(
    action: string,
    params: Record<string, unknown>,
    result?: string,
    error?: string,
    replayParams?: Record<string, unknown>,
  ): AuditEntry {
    const entry: AuditEntry = {
      id: crypto.randomUUID(),
      timestamp: Date.now() / 1000,
      action,
      params,
      sessionId: this.sessionId,
      result,
      error,
      replayParams,
    };
    this.log.push(entry);
    return entry;
  }

  exportAuditLog(): AuditEntry[] {
    return [...this.log];
  }

  summary(): string {
    const counts: Record<string, number> = {};
    for (const e of this.log) counts[e.action] = (counts[e.action] ?? 0) + 1;
    const parts = Object.entries(counts)
      .sort()
      .map(([k, v]) => `${v}x ${k}`)
      .join(", ");
    return `session=${this.sessionId.slice(0, 12)}… | ${this.log.length} actions: ${parts || "none"}`;
  }
}

const sandboxes = new Map<string, ComputerSandbox>();

export function getSandbox(sessionId: string): ComputerSandbox {
  if (!sandboxes.has(sessionId)) sandboxes.set(sessionId, new ComputerSandbox(sessionId));
  return sandboxes.get(sessionId)!;
}

export function configureSandbox(
  sessionId: string,
  opts: { allowedApps?: string[]; screenRegion?: [number, number, number, number] },
): ComputerSandbox {
  const sandbox = getSandbox(sessionId);
  if (opts.allowedApps) sandbox.allowedApps = opts.allowedApps;
  if (opts.screenRegion) sandbox.screenRegion = opts.screenRegion;
  return sandbox;
}
