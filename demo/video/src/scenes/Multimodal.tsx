import React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  useCurrentFrame,
} from "remotion";
import { COLORS, SCENE, sec } from "../config";
import { MONO } from "../font";
import { Caption } from "../components/Caption";
import { FileGlyph, FileKind } from "../components/FileGlyph";
import { SceneFrame } from "../components/SceneFrame";
import { Terminal, TerminalLine } from "../components/Terminal";

const PIPE_X = 960;
const PIPE_Y = 240;
const TRAVEL = sec(1.5);

type FlowFile = { kind: FileKind; source: string; startY: number; delay: number };

const FILES: readonly FlowFile[] = [
  { kind: "pdf", source: "report-06.pdf", startY: 66, delay: sec(0.4) },
  { kind: "png", source: "scan-114.png", startY: 172, delay: sec(0.9) },
  { kind: "mp3", source: "call-03.mp3", startY: 278, delay: sec(1.4) },
  { kind: "mp4", source: "demo-clip.mp4", startY: 384, delay: sec(1.9) },
];

const arrival = (f: FlowFile): number => f.delay + TRAVEL;

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

const FlowingFile: React.FC<{ file: FlowFile }> = ({ file }) => {
  const frame = useCurrentFrame();
  const t = interpolate(frame, [file.delay, arrival(file)], [0, 1], {
    ...clamp,
    easing: Easing.inOut(Easing.cubic),
  });
  const x = interpolate(t, [0, 1], [170, PIPE_X - 46]);
  const y = interpolate(t, [0, 1], [file.startY, PIPE_Y - 50]);
  const scale = interpolate(t, [0, 0.7, 1], [1, 0.9, 0.4]);
  const opacity = interpolate(t, [0, 0.05, 0.82, 1], [0, 1, 1, 0]);
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        transform: `scale(${scale})`,
        opacity,
      }}
    >
      <FileGlyph kind={file.kind} />
    </div>
  );
};

const RecordChip: React.FC<{ file: FlowFile; index: number }> = ({
  file,
  index,
}) => {
  const frame = useCurrentFrame();
  const at = arrival(file) + sec(0.15);
  const t = interpolate(frame, [at, at + sec(0.8)], [0, 1], {
    ...clamp,
    easing: Easing.out(Easing.cubic),
  });
  if (frame < at) {
    return null;
  }
  const x = interpolate(t, [0, 1], [PIPE_X + 40, PIPE_X + 190]);
  const y = 96 + index * 78;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        opacity: t,
        fontFamily: MONO,
        fontSize: 23,
        padding: "10px 18px",
        borderRadius: 9,
        border: `1px solid ${COLORS.panelBorder}`,
        backgroundColor: "rgba(17,20,28,0.92)",
        whiteSpace: "pre",
      }}
    >
      <span style={{ color: COLORS.faint }}>{'{'}</span>
      <span style={{ color: COLORS.ghost }}>{'"__source"'}</span>
      <span style={{ color: COLORS.faint }}>:</span>
      <span style={{ color: COLORS.greenDim }}>{`"${file.source}"`}</span>
      <span style={{ color: COLORS.faint }}>{'}'}</span>
    </div>
  );
};

const PipeGlyph: React.FC = () => {
  const frame = useCurrentFrame();
  const bump = Math.max(
    ...FILES.map((f) =>
      interpolate(frame, [arrival(f) - 3, arrival(f) + 4, arrival(f) + 16], [0, 1, 0], clamp),
    ),
    0,
  );
  const appear = interpolate(frame, [sec(0.15), sec(0.7)], [0, 1], clamp);
  return (
    <div
      style={{
        position: "absolute",
        left: PIPE_X - 14,
        top: PIPE_Y - 150,
        width: 28,
        height: 300,
        borderRadius: 14,
        background: `linear-gradient(180deg, ${COLORS.cyan}, ${COLORS.green})`,
        boxShadow: `0 0 ${34 + bump * 46}px ${8 + bump * 12}px rgba(34,211,238,${0.28 + bump * 0.3})`,
        opacity: appear,
        transform: `scale(${1 + bump * 0.1})`,
      }}
    />
  );
};

const LINES: readonly TerminalLine[] = [
  {
    kind: "cmd",
    at: sec(5.2),
    text: 'smartpipe recordings/*.mp3 | smartpipe map "List every commitment made"',
  },
  {
    kind: "out",
    at: sec(8.2),
    text: '{"result":"Dana: revised SOW to the client by Friday","__source":"call-03.mp3 §00:10-00:20"}',
  },
  {
    kind: "out",
    at: sec(8.6),
    text: '{"result":"Sam: escalate the refund to finance today","__source":"call-03.mp3 §14:02-14:19"}',
  },
  { kind: "note", at: sec(9.6), text: "note: run: 12 items · audio sent natively · 18,344 tokens" },
];

/** Scene 3 — multimodal: files flow into the pipe and come out as records. */
export const Multimodal: React.FC = () => {
  return (
    <SceneFrame duration={SCENE.multimodal}>
      {/* flow diagram, top half */}
      <AbsoluteFill>
        {/* faint guide the files travel along */}
        <div
          style={{
            position: "absolute",
            left: 190,
            top: PIPE_Y - 1,
            width: PIPE_X - 66 - 190,
            borderTop: "2px dashed rgba(103,232,249,0.14)",
          }}
        />
        <PipeGlyph />
        {FILES.map((f) => (
          <FlowingFile key={f.kind} file={f} />
        ))}
        {FILES.map((f, i) => (
          <RecordChip key={f.kind} file={f} index={i} />
        ))}
      </AbsoluteFill>
      {/* terminal, lower half */}
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-end" }}>
        <div style={{ marginBottom: 190 }}>
          <Terminal
            title="~/recordings — smartpipe"
            lines={LINES}
            width={1660}
            height={290}
            fontSize={23}
          />
        </div>
      </AbsoluteFill>
      <Caption at={sec(10.8)} text="It sees, hears, and watches - natively." />
    </SceneFrame>
  );
};
