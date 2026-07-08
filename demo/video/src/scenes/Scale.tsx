import React from "react";
import { AbsoluteFill } from "remotion";
import { SCENE, sec } from "../config";
import { Caption } from "../components/Caption";
import { SceneFrame } from "../components/SceneFrame";
import { Terminal, TerminalLine } from "../components/Terminal";

const LINES: readonly TerminalLine[] = [
  {
    kind: "cmd",
    at: sec(0.3),
    text: "smartpipe embed 'sessions/**/*.mp4' > lib.embeddings",
  },
  {
    kind: "note",
    at: sec(2.6),
    text: "note: run: 214 items · embeddings computed locally (fastembed)",
  },
  {
    kind: "cmd",
    at: sec(3.6),
    text: 'smartpipe top_k 3 --near "user hits the checkout bug" < lib.embeddings',
  },
  { kind: "out", at: sec(6.6), text: '{"__score":0.93,"__source":"sessions/2026-06-12/rec-041.mp4"}' },
  { kind: "out", at: sec(6.9), text: '{"__score":0.88,"__source":"sessions/2026-06-19/rec-102.mp4"}' },
  { kind: "out", at: sec(7.2), text: '{"__score":0.84,"__source":"sessions/2026-05-30/rec-007.mp4"}' },
];

/** Scene 5 — scale: index a folder of videos once, search it by meaning. */
export const Scale: React.FC = () => {
  return (
    <SceneFrame duration={SCENE.scale}>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <div style={{ marginBottom: 120 }}>
          <Terminal
            title="~/research — smartpipe"
            lines={LINES}
            width={1620}
            height={430}
            fontSize={25}
          />
        </div>
      </AbsoluteFill>
      <Caption at={sec(8.6)} text="Search a folder of videos by meaning. No database." />
    </SceneFrame>
  );
};
