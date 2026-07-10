import React from "react";
import { AbsoluteFill, Composition } from "remotion";
import { FPS, HEIGHT, SCENE, TOTAL_FRAMES, WIDTH } from "./config";
import { Background } from "./components/Background";
import { Main } from "./Main";
import { Close } from "./scenes/Close";
import { ColdOpen } from "./scenes/ColdOpen";
import { CostHonesty } from "./scenes/CostHonesty";
import { Graph } from "./scenes/Graph";
import { Hook } from "./scenes/Hook";
import { Multimodal } from "./scenes/Multimodal";
import { Scale } from "./scenes/Scale";

/** Standalone scene = background + scene, so each renders on its own. */
const standalone = (Scene: React.FC): React.FC => {
  const Standalone: React.FC = () => (
    <AbsoluteFill>
      <Background />
      <Scene />
    </AbsoluteFill>
  );
  return Standalone;
};

const SCENES: readonly { id: string; component: React.FC; duration: number }[] = [
  { id: "ColdOpen", component: standalone(ColdOpen), duration: SCENE.coldOpen },
  { id: "Hook", component: standalone(Hook), duration: SCENE.hook },
  { id: "Multimodal", component: standalone(Multimodal), duration: SCENE.multimodal },
  { id: "CostHonesty", component: standalone(CostHonesty), duration: SCENE.cost },
  { id: "Scale", component: standalone(Scale), duration: SCENE.scale },
  { id: "Graph", component: standalone(Graph), duration: SCENE.graph },
  { id: "Close", component: standalone(Close), duration: SCENE.close },
];

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="Main"
        component={Main}
        durationInFrames={TOTAL_FRAMES}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
      />
      {/* The narrated cut: same timeline + Rime voiceover, subtitles, ducked
          music. Needs public/narration/*.wav — run scripts/fetch-narration.mjs. */}
      <Composition
        id="MainNarrated"
        component={Main}
        durationInFrames={TOTAL_FRAMES}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{ narrated: true }}
      />
      {SCENES.map((s) => (
        <Composition
          key={s.id}
          id={s.id}
          component={s.component}
          durationInFrames={s.duration}
          fps={FPS}
          width={WIDTH}
          height={HEIGHT}
        />
      ))}
    </>
  );
};
