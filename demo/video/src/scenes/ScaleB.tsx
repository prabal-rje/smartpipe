import React from "react";
import { AbsoluteFill } from "remotion";
import { SCENE, sec } from "../config";
import { Caption } from "../components/Caption";
import { SceneFrame } from "../components/SceneFrame";
import { Terminal, TerminalLine } from "../components/Terminal";

/** Slide B — search it. Two human blocks (io/writers.py look), kept minimal:
 *  the text and its similarity score, nothing else. */
const LINES: readonly TerminalLine[] = [
  {
    kind: "cmd",
    at: sec(0.3),
    text: 'smartpipe top_k 2 --near "user hits the checkout bug" < lib.embeddings',
  },
  { kind: "ord", at: sec(3.2), text: "#1" },
  { kind: "field", at: sec(3.3), k: "text", v: "Checkout 502 bug …" },
  { kind: "field", at: sec(3.4), k: "__score", v: "0.93" },
  { kind: "ord", at: sec(3.9), text: "#2", gap: true },
  { kind: "field", at: sec(4.0), k: "text", v: "Pay page hangs …" },
  { kind: "field", at: sec(4.1), k: "__score", v: "0.88" },
];

/** Scene 6 — scale B: search the index by meaning. */
export const ScaleB: React.FC = () => {
  return (
    <SceneFrame duration={SCENE.scaleB}>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <div style={{ marginBottom: 120 }}>
          <Terminal
            title="~/research — smartpipe"
            lines={LINES}
            width={1620}
            height={440}
            fontSize={23}
          />
        </div>
      </AbsoluteFill>
      <Caption at={sec(6.2)} text="Search a folder by meaning." />
    </SceneFrame>
  );
};
