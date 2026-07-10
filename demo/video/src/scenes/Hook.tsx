import React from "react";
import { AbsoluteFill } from "remotion";
import { SCENE, sec } from "../config";
import { Caption } from "../components/Caption";
import { SceneFrame } from "../components/SceneFrame";
import { Terminal, TerminalLine } from "../components/Terminal";

/** The TTY human block view (io/writers.py::_HumanWriter): cyan ordinals,
 *  dim keys, the `__source` spine shown once — stated, then trusted. */
const LINES: readonly TerminalLine[] = [
  {
    kind: "cmd",
    at: sec(0.4),
    text: "smartpipe map \"Extract {vendor, total}\" 'invoices/*.pdf'",
  },
  { kind: "ord", at: sec(2.9), text: "#1" },
  { kind: "field", at: sec(3.0), k: "vendor", v: "Acme Corp" },
  { kind: "field", at: sec(3.1), k: "total", v: "1250" },
  { kind: "field", at: sec(3.25), k: "__source", v: "invoices/acme-0642.pdf", dim: true },
  { kind: "ord", at: sec(3.7), text: "#2", gap: true },
  { kind: "field", at: sec(3.8), k: "vendor", v: "Northwind Traders" },
  { kind: "field", at: sec(3.9), k: "total", v: "842.5" },
  { kind: "ord", at: sec(4.3), text: "#3", gap: true },
  { kind: "field", at: sec(4.4), k: "vendor", v: "Globex Ltd" },
  { kind: "field", at: sec(4.5), k: "total", v: "3199" },
  { kind: "note", at: sec(5.2), text: "note: run: ↑4.8k ↓210 tok", gap: true },
];

/** Scene 2 — the hook: one command over a folder of PDFs, readable blocks out. */
export const Hook: React.FC = () => {
  return (
    <SceneFrame duration={SCENE.hook}>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <div style={{ marginBottom: 130 }}>
          <Terminal
            title="~/finance — smartpipe"
            lines={LINES}
            width={1620}
            height={640}
            fontSize={23}
          />
        </div>
      </AbsoluteFill>
      <Caption at={sec(6.4)} text="Point it at a folder. Ask in English. Get data." />
    </SceneFrame>
  );
};
