/**
 * Fetch the narration clips from Rime.AI and write render-ready wavs.
 *
 * Usage:
 *   RIME_API_KEY=... node scripts/fetch-narration.mjs
 *
 * Reads src/narration/lines.json (the committed script), synthesizes one clip
 * per line (speaker "cupola", model "coda"), converts each to 48 kHz mono wav
 * in public/narration/, and writes measured durations (seconds) to
 * src/narration/durations.json so captions and music ducking stay in sync.
 *
 * The key is read from the environment only; it is never logged, and the
 * wavs are gitignored — the repo carries only this script and the text.
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const linesPath = join(root, "src", "narration", "lines.json");
const durationsPath = join(root, "src", "narration", "durations.json");
const outDir = join(root, "public", "narration");

const key = (process.env.RIME_API_KEY ?? "").replace(/^['"]|['"]$/g, "");
if (!key) {
  console.error("RIME_API_KEY is not set");
  process.exit(1);
}

const lines = JSON.parse(readFileSync(linesPath, "utf8"));
mkdirSync(outDir, { recursive: true });

const extensionFor = (contentType) => {
  if (contentType.includes("wav")) return "wav";
  if (contentType.includes("ogg")) return "ogg";
  if (contentType.includes("aac")) return "aac";
  return "mp3"; // audio/mp3, audio/mpeg, and the sensible default
};

const synthesize = async (line) => {
  const response = await fetch("https://users.rime.ai/v1/rime-tts", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      Accept: "audio/mp3",
    },
    body: JSON.stringify({
      speaker: "cupola",
      modelId: "coda",
      lang: "eng",
      text: line.text,
    }),
  });
  if (!response.ok) {
    throw new Error(`${line.id}: HTTP ${response.status}`);
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    // JSON envelope: the audio is a base64 field (audioContent per Rime docs).
    const payload = await response.json();
    const b64 = payload.audioContent ?? payload.audio ?? payload.data;
    if (typeof b64 !== "string") {
      throw new Error(`${line.id}: JSON response without audio content`);
    }
    return { bytes: Buffer.from(b64, "base64"), ext: "mp3" };
  }
  return {
    bytes: Buffer.from(await response.arrayBuffer()),
    ext: extensionFor(contentType),
  };
};

const durations = {};
for (const line of lines) {
  const { bytes, ext } = await synthesize(line);
  const rawPath = join(outDir, `${line.id}.raw.${ext}`);
  const wavPath = join(outDir, `${line.id}.wav`);
  writeFileSync(rawPath, bytes);
  execFileSync("ffmpeg", [
    "-y", "-hide_banner", "-loglevel", "error",
    "-i", rawPath,
    "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
    wavPath,
  ]);
  rmSync(rawPath);
  const seconds = Number(
    execFileSync("ffprobe", [
      "-v", "error",
      "-show_entries", "format=duration",
      "-of", "default=noprint_wrappers=1:nokey=1",
      wavPath,
    ]).toString().trim(),
  );
  durations[line.id] = Math.round(seconds * 1000) / 1000;
  console.log(`${line.id}: ${bytes.length} bytes -> ${durations[line.id]}s`);
  await new Promise((resolve) => setTimeout(resolve, 300)); // be polite
}

writeFileSync(durationsPath, `${JSON.stringify(durations, null, 2)}\n`);
console.log(`wrote ${durationsPath}`);
