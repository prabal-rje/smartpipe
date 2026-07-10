import React from "react";
import { AbsoluteFill } from "remotion";
import { SCENE, sec } from "../config";
import { Caption } from "../components/Caption";
import { SceneFrame } from "../components/SceneFrame";
import { Terminal, TerminalLine } from "../components/Terminal";

const LINES: readonly TerminalLine[] = [
  {
    kind: "cmd",
    at: sec(0.4),
    text: 'smartpipe map "Extract {vendor, total}" invoices/*.pdf',
  },
  { kind: "out", at: sec(2.9), text: '{"vendor":"Acme Corp","total":1250,"__source":{"path":"invoices/acme-0642.pdf","as":"file"}}' },
  { kind: "out", at: sec(3.2), text: '{"vendor":"Northwind Traders","total":842.5,"__source":{"path":"invoices/northwind-0611.pdf","as":"file"}}' },
  { kind: "out", at: sec(3.5), text: '{"vendor":"Globex Ltd","total":3199,"__source":{"path":"invoices/globex-0587.pdf","as":"file"}}' },
  { kind: "out", at: sec(3.8), text: '{"vendor":"Initech Supply","total":268.4,"__source":{"path":"invoices/initech-0629.pdf","as":"file"}}' },
  { kind: "out", at: sec(4.1), text: '{"vendor":"Vandelay Industries","total":5620,"__source":{"path":"invoices/vandelay-0640.pdf","as":"file"}}' },
  { kind: "note", at: sec(5.0), text: "note: run: ↑4.8k ↓210 tok" },
];

/** Scene 2 — the hook: one command over a folder of PDFs, structured rows out. */
export const Hook: React.FC = () => {
  return (
    <SceneFrame duration={SCENE.hook}>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <div style={{ marginBottom: 130 }}>
          <Terminal
            title="~/finance — smartpipe"
            lines={LINES}
            width={1620}
            height={460}
            fontSize={23}
          />
        </div>
      </AbsoluteFill>
      <Caption at={sec(6.4)} text="Point it at a folder. Ask in English. Get data." />
    </SceneFrame>
  );
};
