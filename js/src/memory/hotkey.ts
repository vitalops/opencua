/**
 * Global pause hotkey for the screen-memory daemon.
 *
 * Node has no built-in global key hook, so this uses the optional
 * `uiohook-napi` package (prebuilt for macOS / Windows / Linux).  Install it
 * with `npm install uiohook-napi` to enable the hotkey; without it the
 * daemon still runs and pause works via the CLI / tool.
 *
 * Combo syntax matches the Python SDK (pynput style) so the shared
 * config.json works for both: `<cmd>+<shift>+<alt>+p`, `<ctrl>+<alt>+m`.
 */

export interface HotkeyHandle {
  stop(): void;
}

interface ParsedCombo {
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
  meta: boolean;
  key: string;
}

export function parseCombo(combo: string): ParsedCombo {
  const out: ParsedCombo = { ctrl: false, shift: false, alt: false, meta: false, key: "" };
  for (const raw of combo.split("+")) {
    const part = raw.trim().toLowerCase();
    if (!part) continue;
    switch (part) {
      case "<ctrl>": case "<ctrl_l>": case "<ctrl_r>": case "ctrl": case "control": out.ctrl = true; break;
      case "<shift>": case "<shift_l>": case "<shift_r>": case "shift": out.shift = true; break;
      case "<alt>": case "<alt_l>": case "<alt_r>": case "<option>": case "alt": case "option": out.alt = true; break;
      case "<cmd>": case "<cmd_l>": case "<cmd_r>": case "<super>": case "<win>": case "cmd": case "meta": case "super": out.meta = true; break;
      default:
        out.key = part.replace(/^<|>$/g, "");
    }
  }
  if (!out.key) throw new Error(`hotkey '${combo}' has no non-modifier key`);
  return out;
}

/** uiohook keycodes for the keys we support (letters, digits, F-keys, a few named keys). */
function keycodeFor(key: string, UiohookKey: Record<string, number>): number {
  const k = key.length === 1 ? key.toUpperCase() : key[0].toUpperCase() + key.slice(1).toLowerCase();
  const direct = UiohookKey[k];
  if (typeof direct === "number") return direct;
  const named: Record<string, string> = {
    space: "Space", enter: "Enter", return: "Enter", esc: "Escape", escape: "Escape",
    tab: "Tab", backspace: "Backspace", delete: "Delete", insert: "Insert",
    home: "Home", end: "End", pageup: "PageUp", pagedown: "PageDown",
    up: "ArrowUp", down: "ArrowDown", left: "ArrowLeft", right: "ArrowRight",
  };
  const alias = named[key.toLowerCase()];
  if (alias && typeof UiohookKey[alias] === "number") return UiohookKey[alias];
  if (/^\d$/.test(key)) return UiohookKey[key];
  if (/^f\d{1,2}$/i.test(key)) return UiohookKey["F" + key.slice(1)];
  throw new Error(`unsupported hotkey key '${key}'`);
}

export async function registerHotkey(combo: string, onTrigger: () => void): Promise<HotkeyHandle> {
  const parsed = parseCombo(combo);
  let mod: { uIOhook: { on(ev: string, fn: (e: unknown) => void): void; start(): void; stop(): void }; UiohookKey: Record<string, number> };
  try {
    // Optional dependency — resolved at runtime only.
    mod = (await import("uiohook-napi" as string)) as typeof mod;
  } catch {
    throw new Error("uiohook-napi is not installed (npm install uiohook-napi)");
  }
  const code = keycodeFor(parsed.key, mod.UiohookKey);
  let last = 0;
  const handler = (e: unknown) => {
    const ev = e as { keycode: number; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean };
    if (ev.keycode !== code) return;
    if (!!ev.ctrlKey !== parsed.ctrl || !!ev.shiftKey !== parsed.shift ||
        !!ev.altKey !== parsed.alt || !!ev.metaKey !== parsed.meta) return;
    const t = Date.now();
    if (t - last < 300) return; // debounce key repeat
    last = t;
    try { onTrigger(); } catch { /* never crash the hook */ }
  };
  mod.uIOhook.on("keydown", handler);
  mod.uIOhook.start();
  return { stop: () => { try { mod.uIOhook.stop(); } catch { /* ignore */ } } };
}
