/**
 * Image helpers for screen memory — thumbnails and duplicate detection.
 * Pure JS via jimp (no native modules).
 */

type JimpModule = typeof import("jimp");

async function loadJimp(): Promise<JimpModule> {
  // jimp 0.22 is CommonJS (`export =`); under ESM the constructor lands on
  // `.default`, under CJS it is the module itself.
  const mod = (await import("jimp")) as unknown as JimpModule & { default?: JimpModule };
  return mod.default ?? mod;
}

export interface Thumbnail {
  jpeg: Buffer;
  width: number;
  height: number;
}

/** Downscale a PNG to a JPEG thumbnail no wider than `width`. */
export async function makeThumbnail(png: Buffer, width = 480, quality = 60): Promise<Thumbnail> {
  const Jimp = await loadJimp();
  const img = await Jimp.read(png);
  if (img.bitmap.width > width) img.resize(width, Jimp.AUTO);
  img.quality(quality);
  const jpeg = await img.getBufferAsync(Jimp.MIME_JPEG);
  return { jpeg, width: img.bitmap.width, height: img.bitmap.height };
}

/** Tiny grayscale fingerprint (size×size) used for duplicate detection. */
export async function frameSignature(png: Buffer, size = 16): Promise<number[]> {
  const Jimp = await loadJimp();
  const img = await Jimp.read(png);
  img.resize(size, size).greyscale();
  const out: number[] = [];
  const data = img.bitmap.data;
  for (let i = 0; i < data.length; i += 4) out.push(data[i]);
  return out;
}

/** Mean absolute difference between two signatures, normalised to 0–1. */
export function signatureDelta(a: number[] | null, b: number[] | null): number {
  if (!a || !b || a.length !== b.length || a.length === 0) return 1;
  let total = 0;
  for (let i = 0; i < a.length; i++) total += Math.abs(a[i] - b[i]);
  return total / (255 * a.length);
}

/** Image dimensions of a PNG buffer (from the IHDR chunk, no decode). */
export function pngSize(png: Buffer): { width: number; height: number } | null {
  if (png.length < 24 || png.toString("ascii", 1, 4) !== "PNG") return null;
  return { width: png.readUInt32BE(16), height: png.readUInt32BE(20) };
}
