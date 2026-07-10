import React from "react";
import { AbsoluteFill, Audio, interpolate, Series, staticFile } from "remotion";
import { SCENE, TOTAL_FRAMES } from "./config";
import { Background } from "./components/Background";
import { Close } from "./scenes/Close";
import { ColdOpen } from "./scenes/ColdOpen";
import { CostHonesty } from "./scenes/CostHonesty";
import { Graph } from "./scenes/Graph";
import { Hook } from "./scenes/Hook";
import { Multimodal } from "./scenes/Multimodal";
import { Scale } from "./scenes/Scale";
import { duckFactor, Narration } from "./Narration";

/** Music bed base gain. Owner order (2026-07-09): "lower BG volume much
 *  more" — 0.55 → 0.14, about −12 dB. Texture, never presence. */
const MUSIC_BASE = 0.14;

export type MainProps = { narrated?: boolean };

/** The full 80s cut: one persistent background, seven scenes in series.
 *  With `narrated`, the Rime voiceover plays on top, a subtitle band shows
 *  the spoken words, and the music ducks a further few dB under speech. */
export const Main: React.FC<MainProps> = ({ narrated = false }) => {
  return (
    <AbsoluteFill>
      {/* Music bed: Silicon Prism Waltz (60s, looped to cover the 80s cut).
          Volume envelope: quick fade-in, steady bed, fade-out over the close.
          A fully synthesized fallback lives at scripts/make-music.mjs. */}
      <Audio
        loop
        src={staticFile("silicon-prism-waltz.m4a")}
        volume={(f) =>
          MUSIC_BASE *
          (narrated ? duckFactor(f) : 1) *
          interpolate(
            f,
            [0, 36, TOTAL_FRAMES - 96, TOTAL_FRAMES - 8],
            [0, 1, 1, 0],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
          )
        }
      />
      <Background />
      <Series>
        <Series.Sequence durationInFrames={SCENE.coldOpen}>
          <ColdOpen />
        </Series.Sequence>
        <Series.Sequence durationInFrames={SCENE.hook}>
          <Hook />
        </Series.Sequence>
        <Series.Sequence durationInFrames={SCENE.multimodal}>
          <Multimodal />
        </Series.Sequence>
        <Series.Sequence durationInFrames={SCENE.cost}>
          <CostHonesty />
        </Series.Sequence>
        <Series.Sequence durationInFrames={SCENE.scale}>
          <Scale />
        </Series.Sequence>
        <Series.Sequence durationInFrames={SCENE.graph}>
          <Graph />
        </Series.Sequence>
        <Series.Sequence durationInFrames={SCENE.close}>
          <Close />
        </Series.Sequence>
      </Series>
      {narrated ? <Narration /> : null}
    </AbsoluteFill>
  );
};
