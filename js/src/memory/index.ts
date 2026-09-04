/**
 * opendesk screen memory — a local, searchable desktop history.
 *
 * A low-frequency background loop captures the screen, runs OCR locally,
 * and stores the text plus a small thumbnail in `~/.opendesk/memory`.  The
 * `memory` tool lets an agent search that history.  Nothing leaves the machine.
 */

export {
  MemoryConfig,
  type MemoryConfigData,
  type PauseState,
  type DaemonState,
  memoryDir,
  loadConfig,
  saveConfig,
  getPause,
  setPause,
  clearPause,
  isPaused,
  describePause,
  parseDuration,
  daemonAlive,
  readDaemonState,
} from "./config.js";
export { MemoryStore, type Frame, type StoreStats, type QueryOpts, queryTerms, makeSnippet, frameWhen } from "./store.js";
export { parseWhen, parseRange, fmtTs, fmtDay, fmtRange, type TimeRange } from "./timeparse.js";
export { ScreenMemoryRecorder, startDaemon, type RecorderOptions, type CaptureFn, type OcrFn } from "./recorder.js";
export { makeThumbnail, frameSignature, signatureDelta, type Thumbnail } from "./image.js";
export { createOcrEngine, availableBackend, ensureVisionHelper, type OcrEngine, type OcrPreference } from "./ocr.js";
export { frontmost, type Frontmost } from "./frontmost.js";
export { installMemoryService, uninstallMemoryService, renderLaunchdPlist, renderSystemdUnit, renderSchtasksCommand } from "./service.js";
export { parseCombo, registerHotkey, type HotkeyHandle } from "./hotkey.js";
