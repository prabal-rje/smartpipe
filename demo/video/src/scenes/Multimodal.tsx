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
  { kind: "pdf", source: "report-06.pdf", startY: 66, delay: sec(1.2) },
  { kind: "png", source: "scan-114.png", startY: 172, delay: sec(1.7) },
  { kind: "mp3", source: "call-03.mp3", startY: 278, delay: sec(2.2) },
  { kind: "mp4", source: "demo-clip.mp4", startY: 384, delay: sec(2.7) },
];

/** Right side, per file: a small modality visual + one plain answer line. */
const ANSWERS: Readonly<Record<FileKind, string>> = {
  pdf: "audit due by March 31",
  png: "net-30 terms, signed",
  mp3: "promised a refund by Friday",
  mp4: "demo re-shoot promised Monday",
};

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

/** A plausible pixel-block thumbnail (io/preview.py's plotext half-block
 *  look), drawn from a fixed pattern in the OG palette. */
const THUMB_PATTERN: readonly (readonly number[])[] = [
  [0, 1, 1, 2, 2, 1, 0, 0, 1, 1],
  [1, 2, 3, 3, 2, 2, 1, 1, 2, 1],
  [1, 3, 3, 2, 3, 3, 2, 2, 2, 0],
  [0, 2, 3, 3, 3, 2, 3, 3, 1, 0],
  [0, 1, 2, 2, 1, 1, 2, 1, 1, 0],
];

const THUMB_SHADES: readonly string[] = [
  "rgba(30,36,48,0.9)",
  "rgba(34,211,238,0.22)",
  "rgba(52,211,153,0.30)",
  "rgba(103,232,249,0.45)",
];

const Thumb: React.FC<{ play?: boolean }> = ({ play = false }) => (
  <div style={{ position: "relative" }}>
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: 4,
        borderRadius: 5,
        border: `1px solid ${COLORS.panelBorder}`,
        backgroundColor: "rgba(12,14,18,0.9)",
      }}
    >
      {THUMB_PATTERN.map((row, r) => (
        <div key={r} style={{ display: "flex", gap: 2 }}>
          {row.map((cell, c) => (
            <div
              key={c}
              style={{
                width: 9,
                height: 8,
                borderRadius: 1,
                backgroundColor: THUMB_SHADES[cell],
              }}
            />
          ))}
        </div>
      ))}
    </div>
    {play ? (
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: COLORS.text,
          fontSize: 22,
          textShadow: "0 0 8px rgba(0,0,0,0.9)",
        }}
      >
        ▶
      </div>
    ) : null}
  </div>
);

/** The audio modality visual: a small peak-envelope row. */
const Waveform: React.FC = () => (
  <div
    style={{
      fontFamily: MONO,
      fontSize: 19,
      color: COLORS.cyanDim,
      opacity: 0.7,
    }}
  >
    ▁▂▅▇▅▃▆▇▄▂
  </div>
);

/** The text/pdf visual: a few dim scribble lines suggesting a paragraph. */
const Scribble: React.FC = () => (
  <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
    {[74, 58, 66].map((w) => (
      <div
        key={w}
        style={{
          width: w,
          height: 4,
          borderRadius: 2,
          backgroundColor: COLORS.faint,
          opacity: 0.55,
        }}
      />
    ))}
  </div>
);

const Visual: React.FC<{ kind: FileKind }> = ({ kind }) => {
  switch (kind) {
    case "pdf":
      return <Scribble />;
    case "png":
      return <Thumb />;
    case "mp3":
      return <Waveform />;
    case "mp4":
      return <Thumb play />;
  }
};

/** One answer card: the modality visual + the one extracted answer line. */
const AnswerCard: React.FC<{ file: FlowFile; index: number }> = ({
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
  const x = interpolate(t, [0, 1], [PIPE_X + 40, PIPE_X + 170]);
  const y = 62 + index * 96;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        opacity: t,
        display: "flex",
        alignItems: "center",
        gap: 20,
        padding: "12px 20px",
        borderRadius: 10,
        border: `1px solid ${COLORS.panelBorder}`,
        backgroundColor: "rgba(17,20,28,0.92)",
        fontFamily: MONO,
        whiteSpace: "pre",
      }}
    >
      <div style={{ width: 118, display: "flex", justifyContent: "center" }}>
        <Visual kind={file.kind} />
      </div>
      <div style={{ fontSize: 23, color: COLORS.text }}>
        {ANSWERS[file.kind]}
      </div>
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

/** The terminal that drives it: the typed command and the verbatim receipt. */
const LINES: readonly TerminalLine[] = [
  {
    kind: "cmd",
    at: sec(0.3),
    text: "smartpipe map \"List every commitment made\" 'inbox/*'",
  },
  {
    kind: "note",
    at: sec(8.6),
    text: "note: run: ↑18.3k ↓612 tok · 2.1 MB images (1) · 8.4 MB video (1) · 4.1 MB audio (1) · 12m04s",
    gap: true,
  },
];

/** Scene 3 — multimodal: files flow into the pipe; per-modality visuals and
 *  one plain answer per file come out the other side. */
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
          <AnswerCard key={f.kind} file={f} index={i} />
        ))}
      </AbsoluteFill>
      {/* terminal, lower half */}
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-end" }}>
        <div style={{ marginBottom: 190 }}>
          <Terminal
            title="~/inbox — smartpipe"
            lines={LINES}
            width={1660}
            height={220}
            fontSize={22}
          />
        </div>
      </AbsoluteFill>
      <Caption at={sec(10.8)} text="It sees, hears, and watches - natively." />
    </SceneFrame>
  );
};
