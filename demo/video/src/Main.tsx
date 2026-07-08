import React from "react";
import { AbsoluteFill, Audio, interpolate, Series, staticFile } from "remotion";
import { SCENE, TOTAL_FRAMES } from "./config";
import { Background } from "./components/Background";
import { Close } from "./scenes/Close";
import { ColdOpen } from "./scenes/ColdOpen";
import { CostHonesty } from "./scenes/CostHonesty";
import { Hook } from "./scenes/Hook";
import { Multimodal } from "./scenes/Multimodal";
import { Scale } from "./scenes/Scale";

/** The full ~70s cut: one persistent background, six scenes in series. */
export const Main: React.FC = () => {
  return (
    <AbsoluteFill>
      {/* Music bed: Silicon Prism Waltz (60s, looped to cover the 70s cut).
          Volume envelope: quick fade-in, steady bed, fade-out over the close.
          A fully synthesized fallback lives at scripts/make-music.mjs. */}
      <Audio
        loop
        src={staticFile("silicon-prism-waltz.m4a")}
        volume={(f) =>
          0.55 *
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
        <Series.Sequence durationInFrames={SCENE.close}>
          <Close />
        </Series.Sequence>
      </Series>
    </AbsoluteFill>
  );
};
