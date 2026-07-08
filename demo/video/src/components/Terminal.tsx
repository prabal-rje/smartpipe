import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, CPS } from "../config";
import { MONO } from "../font";

/**
 * The one reusable terminal that drives every scene.
 *
 * Lines are declared with a `kind` and a start frame (`at`, relative to the
 * enclosing <Sequence>):
 *   - "cmd"  → typed character-by-character behind a block caret
 *   - "out"  → stdout rows that cascade in with a small spring
 *   - "note" → dimmed stderr diagnostics in yellow (receipts, run notes)
 */
export type TerminalLine =
  | { kind: "cmd"; text: string; at: number; cps?: number }
  | { kind: "out"; text: string; at: number }
  | { kind: "note"; text: string; at: number };

type Span = { text: string; color: string; weight?: number };

/** Order matters: strings, the binary name, flags, shell operators. */
const CMD_TOKEN = /('[^']*'|"[^"]*")|(\bsmartpipe\b)|(\s--[\w-]+)|([|<>]+)/g;

const colorizeCommand = (text: string): Span[] => {
  const spans: Span[] = [];
  let last = 0;
  for (const m of text.matchAll(CMD_TOKEN)) {
    const idx = m.index ?? 0;
    if (idx > last) {
      spans.push({ text: text.slice(last, idx), color: COLORS.text });
    }
    if (m[1] !== undefined) {
      spans.push({ text: m[1], color: COLORS.green });
    } else if (m[2] !== undefined) {
      spans.push({ text: m[2], color: COLORS.cyan, weight: 700 });
    } else if (m[3] !== undefined) {
      spans.push({ text: m[3], color: COLORS.dim });
    } else {
      spans.push({ text: m[0], color: COLORS.cyan, weight: 700 });
    }
    last = idx + m[0].length;
  }
  if (last < text.length) {
    spans.push({ text: text.slice(last), color: COLORS.text });
  }
  return spans;
};

const JSON_TOKEN = /"(?:\\.|[^"\\])*"(\s*:)?|-?\d+(?:\.\d+)?|\btrue\b|\bfalse\b|\bnull\b/g;

const colorizeJson = (text: string): Span[] => {
  const spans: Span[] = [];
  let last = 0;
  for (const m of text.matchAll(JSON_TOKEN)) {
    const idx = m.index ?? 0;
    if (idx > last) {
      spans.push({ text: text.slice(last, idx), color: COLORS.faint });
    }
    const isKey = m[1] !== undefined;
    const isString = m[0].startsWith('"');
    if (isKey) {
      const reserved = m[0].startsWith('"__');
      spans.push({
        text: m[0],
        color: reserved ? COLORS.ghost : COLORS.cyanDim,
      });
    } else if (isString) {
      spans.push({ text: m[0], color: COLORS.greenDim });
    } else {
      spans.push({ text: m[0], color: COLORS.text });
    }
    last = idx + m[0].length;
  }
  if (last < text.length) {
    spans.push({ text: text.slice(last), color: COLORS.faint });
  }
  return spans;
};

const colorizeOut = (text: string): Span[] =>
  text.trimStart().startsWith("{")
    ? colorizeJson(text)
    : [{ text, color: COLORS.text }];

const Spans: React.FC<{ spans: Span[] }> = ({ spans }) => (
  <>
    {spans.map((s, i) => (
      <span key={i} style={{ color: s.color, fontWeight: s.weight ?? 400 }}>
        {s.text}
      </span>
    ))}
  </>
);

const Caret: React.FC<{ visible: boolean; fontSize: number }> = ({
  visible,
  fontSize,
}) => (
  <span
    style={{
      display: "inline-block",
      width: fontSize * 0.58,
      height: fontSize * 1.15,
      marginLeft: 2,
      verticalAlign: "text-bottom",
      backgroundColor: COLORS.cyan,
      boxShadow: `0 0 12px ${COLORS.cyan}`,
      opacity: visible ? 0.95 : 0,
    }}
  />
);

type TerminalProps = {
  title: string;
  lines: readonly TerminalLine[];
  width?: number;
  height?: number;
  fontSize?: number;
};

export const Terminal: React.FC<TerminalProps> = ({
  title,
  lines,
  width = 1560,
  height = 620,
  fontSize = 26,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const blinkOn = Math.floor(frame / 16) % 2 === 0;
  const lineHeight = Math.round(fontSize * 1.72);

  const cmdIndices = lines
    .map((l, i) => ({ l, i }))
    .filter(({ l }) => l.kind === "cmd" && l.at <= frame)
    .map(({ i }) => i);
  const activeCmd = cmdIndices.length > 0 ? cmdIndices[cmdIndices.length - 1] : -1;

  return (
    <div
      style={{
        width,
        height,
        borderRadius: 14,
        border: `1px solid ${COLORS.panelBorder}`,
        backgroundColor: "rgba(17,20,28,0.94)",
        boxShadow:
          "0 30px 80px rgba(0,0,0,0.55), 0 0 0 1px rgba(34,211,238,0.05), 0 0 90px rgba(34,211,238,0.06)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        fontFamily: MONO,
      }}
    >
      {/* title bar with traffic lights */}
      <div
        style={{
          height: 48,
          flexShrink: 0,
          backgroundColor: COLORS.titleBar,
          borderBottom: `1px solid ${COLORS.panelBorder}`,
          display: "flex",
          alignItems: "center",
          padding: "0 20px",
          position: "relative",
        }}
      >
        <div style={{ display: "flex", gap: 9 }}>
          {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
            <div
              key={c}
              style={{
                width: 13,
                height: 13,
                borderRadius: "50%",
                backgroundColor: c,
                opacity: 0.9,
              }}
            />
          ))}
        </div>
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            textAlign: "center",
            color: COLORS.faint,
            fontSize: 17,
            letterSpacing: 0.4,
          }}
        >
          {title}
        </div>
      </div>

      {/* buffer */}
      <div style={{ padding: `${Math.round(fontSize * 0.9)}px 40px`, flex: 1 }}>
        {lines.length === 0 || lines.every((l) => l.at > frame) ? (
          <div style={{ fontSize, lineHeight: `${lineHeight}px` }}>
            <span style={{ color: COLORS.green, fontWeight: 700 }}>{"❯"} </span>
            <Caret visible={blinkOn} fontSize={fontSize} />
          </div>
        ) : null}
        {lines.map((line, i) => {
          if (line.at > frame) {
            return null;
          }
          if (line.kind === "cmd") {
            const cps = line.cps ?? CPS.command;
            const shown = Math.min(
              line.text.length,
              Math.floor(((frame - line.at) * cps) / fps),
            );
            const typing = shown < line.text.length;
            const caretVisible = i === activeCmd && (typing || blinkOn);
            return (
              <div key={i} style={{ fontSize, lineHeight: `${lineHeight}px` }}>
                <span style={{ color: COLORS.green, fontWeight: 700 }}>
                  {"❯"}{" "}
                </span>
                <Spans spans={colorizeCommand(line.text.slice(0, shown))} />
                {i === activeCmd ? (
                  <Caret visible={caretVisible} fontSize={fontSize} />
                ) : null}
              </div>
            );
          }
          const p = spring({
            frame: frame - line.at,
            fps,
            config: { damping: 16, mass: 0.6, stiffness: 130 },
            durationInFrames: 22,
          });
          const opacity = interpolate(frame - line.at, [0, 7], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const style: React.CSSProperties = {
            fontSize,
            lineHeight: `${lineHeight}px`,
            transform: `translateY(${(1 - p) * 15}px)`,
            opacity,
            whiteSpace: "pre",
          };
          if (line.kind === "note") {
            return (
              <div
                key={i}
                style={{ ...style, color: COLORS.yellow, opacity: opacity * 0.78 }}
              >
                {line.text}
              </div>
            );
          }
          return (
            <div key={i} style={style}>
              <Spans spans={colorizeOut(line.text)} />
            </div>
          );
        })}
      </div>
    </div>
  );
};
