import React from "react";
import { AbsoluteFill, Series } from "remotion";
import { SCENE } from "./config";
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
