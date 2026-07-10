import React from "react";
import { AbsoluteFill } from "remotion";
import { SCENE, sec } from "../config";
import { Caption } from "../components/Caption";
import { SceneFrame } from "../components/SceneFrame";
import { Terminal, TerminalLine } from "../components/Terminal";

/** Slide A — make the index. What came back, as a simple picture: a short
 *  text snippet per row plus a dim, truncated vector glyph. No metadata. */
const LINES: readonly TerminalLine[] = [
  {
    kind: "cmd",
    at: sec(0.3),
    text: "smartpipe embed 'tickets/**/*.md' > lib.embeddings",
  },
  {
    kind: "note",
    at: sec(2.4),
    text: "note: local embeddings: nomic-embed-text v1.5 on CPU (first use downloads ~130 MB, once)",
  },
  { kind: "pair", at: sec(3.6), left: "Checkout 502 bug …", right: "[0.12 -0.87 0.44 …]" },
  { kind: "pair", at: sec(4.0), left: "Pay page hangs …", right: "[0.31 0.08 -0.52 …]" },
];

/** Scene 5 — scale A: embed a folder once, on your own machine. */
export const ScaleA: React.FC = () => {
  return (
    <SceneFrame duration={SCENE.scaleA}>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <div style={{ marginBottom: 120 }}>
          <Terminal
            title="~/research — smartpipe"
            lines={LINES}
            width={1620}
            height={340}
            fontSize={23}
          />
        </div>
      </AbsoluteFill>
      <Caption at={sec(6.0)} text="Index a folder once. It stays on your machine." />
    </SceneFrame>
  );
};
