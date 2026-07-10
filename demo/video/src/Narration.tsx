import React from "react";
import {
  AbsoluteFill,
  Audio,
  interpolate,
  Sequence,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { COLORS, FPS } from "./config";
import { MONO } from "./font";
import lines from "./narration/lines.json";
import durations from "./narration/durations.json";

/**
 * The Rime.AI voiceover: one wav per line of the committed script
 * (src/narration/lines.json), fetched by scripts/fetch-narration.mjs into
 * public/narration/ (gitignored). Each line also renders as a subtitle band
 * below the scene caption cards, and duckFactor() dips the music bed a few
 * dB while anyone is speaking.
 */

type Line = { id: string; at: number; text: string };

const LINES: readonly Line[] = lines;
const DURATIONS: Record<string, number> = durations;

/** Narration mix gain: the clips peak near −1 dBFS — leave a little headroom. */
const NARRATION_GAIN = 0.85;

/** Music multiplier while the voice speaks (≈ −4.4 dB on top of the bed). */
const VOICE_DUCK = 0.6;

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

const clipFrames = (id: string): number => {
  const seconds = DURATIONS[id];
  if (seconds === undefined) {
    throw new Error(`no duration for narration clip "${id}" — run scripts/fetch-narration.mjs`);
  }
  return Math.ceil(seconds * FPS);
};

/** 1 in silence, VOICE_DUCK under speech, with 12-frame ramps either side. */
export const duckFactor = (frame: number): number => {
  const coverage = Math.max(
    ...LINES.map((line) => {
      const end = line.at + clipFrames(line.id);
      return interpolate(frame, [line.at - 12, line.at, end, end + 12], [0, 1, 1, 0], clamp);
    }),
    0,
  );
  return 1 - (1 - VOICE_DUCK) * coverage;
};

/** The spoken words, as a dim mono subtitle band under the caption cards. */
const SubtitleLine: React.FC<{ text: string; duration: number }> = ({
  text,
  duration,
}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 6, duration - 8, duration], [0, 1, 1, 0], clamp);
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center" }}>
      <div
        style={{
          marginBottom: 18,
          fontFamily: MONO,
          fontSize: 24,
          color: COLORS.dim,
          letterSpacing: 0.3,
          opacity: opacity * 0.9,
          textShadow: "0 2px 12px rgba(0,0,0,0.85)",
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

export const Narration: React.FC = () => (
  <AbsoluteFill>
    {LINES.map((line) => {
      const duration = clipFrames(line.id) + 10;
      return (
        <Sequence key={line.id} from={line.at} durationInFrames={duration}>
          <Audio src={staticFile(`narration/${line.id}.wav`)} volume={NARRATION_GAIN} />
          <SubtitleLine text={line.text} duration={duration} />
        </Sequence>
      );
    })}
  </AbsoluteFill>
);
