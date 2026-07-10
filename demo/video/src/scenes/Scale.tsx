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
    text: "smartpipe embed 'tickets/**/*.md' > lib.embeddings",
  },
  {
    kind: "note",
    at: sec(2.6),
    text: "note: local embeddings: nomic-embed-text v1.5 on CPU (first use downloads ~130 MB, once)",
  },
  {
    kind: "cmd",
    at: sec(3.6),
    text: 'smartpipe top_k 3 --near "user hits the checkout bug" < lib.embeddings',
  },
  { kind: "out", at: sec(6.6), text: '{"text":"Checkout 502 bug …","__embedder":"local/nomic-embed-text-v1.5","__source":{"path":"tickets/T-4118.md","as":"file"},"_score":0.93}' },
  { kind: "out", at: sec(6.9), text: '{"text":"Pay page hangs …","__embedder":"local/nomic-embed-text-v1.5","__source":{"path":"tickets/T-3982.md","as":"file"},"_score":0.88}' },
  { kind: "out", at: sec(7.2), text: '{"text":"Cart total wrong …","__embedder":"local/nomic-embed-text-v1.5","__source":{"path":"tickets/T-2077.md","as":"file"},"_score":0.84}' },
];

/** Scene 5 — scale: index a folder of tickets once, search it by meaning. */
export const Scale: React.FC = () => {
  return (
    <SceneFrame duration={SCENE.scale}>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <div style={{ marginBottom: 120 }}>
          <Terminal
            title="~/research — smartpipe"
            lines={LINES}
            width={1780}
            height={430}
            fontSize={20}
          />
        </div>
      </AbsoluteFill>
      <Caption at={sec(8.6)} text="Search a folder of tickets by meaning. No database." />
    </SceneFrame>
  );
};
