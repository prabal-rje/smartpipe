#!/usr/bin/env node
/**
 * Generates the demo's music bed from scratch — an original, deterministic
 * ambient-electronic track (A minor, 100 BPM, 70 s), so the audio is
 * royalty-free by construction: no samples, no third-party recordings.
 *
 * Layers:
 *   pad   — detuned additive chords, slow attack (whole track)
 *   sub   — sine bass on chord roots (enters with the first terminal scene)
 *   arp   — plucked eighth-note chord tones with a ping-pong echo (8 s → 62 s)
 *   air   — lowpassed noise floor, barely audible
 *
 * Usage:  node scripts/make-music.mjs
 * Writes: out/music.wav, then encodes public/music.m4a (requires ffmpeg).
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const SR = 44100;
const DUR = 70;
const N = SR * DUR;
const BPM = 100;
const EIGHTH = 60 / BPM / 2; // 0.3 s
const SEG = 16 * EIGHTH; // one chord = 2 bars = 4.8 s

const midiHz = (m) => 440 * 2 ** ((m - 69) / 12);

// Am7 → Fmaj7 → Cmaj7 → G6, voiced for smooth voice leading.
const PROGRESSION = [
  { pad: [57, 60, 64, 67], root: 33 },
  { pad: [53, 57, 60, 64], root: 29 },
  { pad: [55, 59, 60, 64], root: 36 },
  { pad: [55, 59, 62, 64], root: 31 },
];

const L = new Float64Array(N);
const R = new Float64Array(N);

/** Smooth 0→1 ramp. */
const smooth = (x) => {
  const t = Math.min(1, Math.max(0, x));
  return t * t * (3 - 2 * t);
};

/** Attack/sustain/release envelope over absolute seconds. */
const env = (t, t0, t1, atk, rel) => {
  if (t < t0 || t > t1 + rel) return 0;
  const a = smooth((t - t0) / atk);
  const r = t > t1 ? 1 - smooth((t - t1) / rel) : 1;
  return a * r;
};

// ---- pad + sub -------------------------------------------------------------
const segments = Math.ceil(DUR / SEG);
for (let s = 0; s < segments; s++) {
  const chord = PROGRESSION[s % PROGRESSION.length];
  const t0 = s * SEG;
  const t1 = Math.min(t0 + SEG, DUR);
  const i0 = Math.floor(t0 * SR);
  const i1 = Math.min(N, Math.floor((t1 + 2.0) * SR));
  const padHz = chord.pad.map(midiHz);
  const subHz = midiHz(chord.root);
  for (let i = i0; i < i1; i++) {
    const t = i / SR;
    const e = env(t, t0, t1, 1.4, 1.8);
    if (e === 0) continue;
    let l = 0;
    let r = 0;
    for (const hz of padHz) {
      // detuned pair, ±4 cents, one per channel — wide, slow-beating pad
      const dl = hz * 2 ** (-4 / 1200);
      const dr = hz * 2 ** (4 / 1200);
      l += Math.sin(2 * Math.PI * dl * t) + 0.28 * Math.sin(4 * Math.PI * dl * t);
      r += Math.sin(2 * Math.PI * dr * t) + 0.28 * Math.sin(4 * Math.PI * dr * t);
    }
    const padGain = 0.052 * e;
    // sub enters at 8 s, ducks out over the close
    const subAmp =
      0.16 * e * smooth((t - 8) / 2) * (1 - smooth((t - 63) / 5));
    const sub = Math.sin(2 * Math.PI * subHz * t) * subAmp;
    L[i] += l * padGain + sub;
    R[i] += r * padGain + sub;
  }
}

// ---- arp -------------------------------------------------------------------
const ARP_START = 8;
const ARP_END = 62;
const PATTERN = [0, 2, 1, 3, 2, 0, 3, 1];
const pluck = (start, hz, pan, gain) => {
  const i0 = Math.floor(start * SR);
  const len = Math.floor(1.1 * SR);
  for (let k = 0; k < len && i0 + k < N; k++) {
    const t = k / SR;
    const amp = gain * Math.exp(-t / 0.16) * smooth(t / 0.004);
    const v =
      amp *
      (Math.sin(2 * Math.PI * hz * t) + 0.35 * Math.sin(4 * Math.PI * hz * t));
    L[i0 + k] += v * (1 - pan);
    R[i0 + k] += v * pan;
  }
};
for (let step = 0; ; step++) {
  const t = ARP_START + step * EIGHTH;
  if (t >= ARP_END) break;
  const seg = Math.floor(t / SEG);
  const chord = PROGRESSION[seg % PROGRESSION.length];
  const tone = chord.pad[PATTERN[step % PATTERN.length]] + 12;
  const fadeIn = smooth((t - ARP_START) / 4);
  const fadeOut = 1 - smooth((t - (ARP_END - 6)) / 6);
  const g = 0.085 * fadeIn * fadeOut;
  if (g <= 0.001) continue;
  const pan = step % 2 === 0 ? 0.32 : 0.68;
  pluck(t, midiHz(tone), pan, g);
  // ping-pong echoes on the opposite side, dotted-eighth spacing
  pluck(t + 3 * EIGHTH * 0.5 * 3, midiHz(tone), 1 - pan, g * 0.34);
  pluck(t + 3 * EIGHTH * 3, midiHz(tone), pan, g * 0.13);
}

// ---- air (deterministic lowpassed noise floor) -------------------------------
let seed = 0x5eed;
const rand = () => {
  seed = (seed * 1664525 + 1013904223) >>> 0;
  return seed / 0xffffffff - 0.5;
};
let lpL = 0;
let lpR = 0;
const alpha = 1 - Math.exp((-2 * Math.PI * 700) / SR);
for (let i = 0; i < N; i++) {
  const t = i / SR;
  lpL += alpha * (rand() - lpL);
  lpR += alpha * (rand() - lpR);
  const g = 0.011 * (0.7 + 0.3 * Math.sin(2 * Math.PI * 0.05 * t));
  L[i] += lpL * g;
  R[i] += lpR * g;
}

// ---- master: fades, soft clip, normalize, 16-bit WAV -------------------------
let peak = 0;
for (let i = 0; i < N; i++) {
  const t = i / SR;
  const fade = smooth(t / 2) * (1 - smooth((t - 65) / 4.6));
  L[i] = Math.tanh(L[i] * 1.15) * fade;
  R[i] = Math.tanh(R[i] * 1.15) * fade;
  peak = Math.max(peak, Math.abs(L[i]), Math.abs(R[i]));
}
const norm = 0.82 / peak;

const data = Buffer.alloc(N * 4);
for (let i = 0; i < N; i++) {
  data.writeInt16LE(Math.round(L[i] * norm * 32767), i * 4);
  data.writeInt16LE(Math.round(R[i] * norm * 32767), i * 4 + 2);
}
const header = Buffer.alloc(44);
header.write("RIFF", 0);
header.writeUInt32LE(36 + data.length, 4);
header.write("WAVE", 8);
header.write("fmt ", 12);
header.writeUInt32LE(16, 16);
header.writeUInt16LE(1, 20); // PCM
header.writeUInt16LE(2, 22); // stereo
header.writeUInt32LE(SR, 24);
header.writeUInt32LE(SR * 4, 28);
header.writeUInt16LE(4, 32);
header.writeUInt16LE(16, 34);
header.write("data", 36);
header.writeUInt32LE(data.length, 40);

mkdirSync(join(ROOT, "out"), { recursive: true });
mkdirSync(join(ROOT, "public"), { recursive: true });
const wav = join(ROOT, "out", "music.wav");
writeFileSync(wav, Buffer.concat([header, data]));
console.log(`wrote ${wav}`);

const m4a = join(ROOT, "public", "music.m4a");
execFileSync("ffmpeg", ["-y", "-i", wav, "-c:a", "aac", "-b:a", "160k", m4a], {
  stdio: "inherit",
});
console.log(`wrote ${m4a}`);
