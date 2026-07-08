import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { COLORS } from "../config";
import { MONO } from "../font";

/** figlet -f "ANSI Shadow" "smartpipe" — generated, do not hand-edit. */
const ART: readonly string[] = [
  "███████╗███╗   ███╗ █████╗ ██████╗ ████████╗██████╗ ██╗██████╗ ███████╗",
  "██╔════╝████╗ ████║██╔══██╗██╔══██╗╚══██╔══╝██╔══██╗██║██╔══██╗██╔════╝",
  "███████╗██╔████╔██║███████║██████╔╝   ██║   ██████╔╝██║██████╔╝█████╗  ",
  "╚════██║██║╚██╔╝██║██╔══██║██╔══██╗   ██║   ██╔═══╝ ██║██╔═══╝ ██╔══╝  ",
  "███████║██║ ╚═╝ ██║██║  ██║██║  ██║   ██║   ██║     ██║██║     ███████╗",
  "╚══════╝╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚═╝╚═╝     ╚══════╝",
];

const MAX_COLS = Math.max(...ART.map((l) => l.length));

type WordmarkProps = {
  /** Frame the left-to-right draw starts. */
  drawStart: number;
  /** Frame the draw completes. Equal to drawStart = already drawn. */
  drawEnd: number;
  fontSize?: number;
};

/**
 * The ASCII wordmark, drawn column-by-column left to right with a glowing
 * draw-head that leads the reveal.
 */
export const Wordmark: React.FC<WordmarkProps> = ({
  drawStart,
  drawEnd,
  fontSize = 46,
}) => {
  const frame = useCurrentFrame();
  const cols =
    drawEnd <= drawStart
      ? MAX_COLS
      : interpolate(frame, [drawStart, drawEnd], [0, MAX_COLS], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
  const shown = Math.floor(cols);
  const drawing = shown > 0 && shown < MAX_COLS;
  const charW = fontSize * 0.6;

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <pre
        style={{
          margin: 0,
          fontFamily: MONO,
          fontSize,
          lineHeight: 1.16,
          fontWeight: 400,
          color: COLORS.cyan,
          textShadow:
            "0 0 16px rgba(34,211,238,0.45), 0 0 56px rgba(34,211,238,0.22)",
          whiteSpace: "pre",
        }}
      >
        {ART.map((line) => line.slice(0, shown)).join("\n")}
      </pre>
      {drawing ? (
        <div
          style={{
            position: "absolute",
            top: -8,
            bottom: -8,
            left: shown * charW,
            width: 3,
            backgroundColor: COLORS.cyanDim,
            boxShadow: `0 0 22px 4px ${COLORS.cyan}`,
            opacity: 0.9,
          }}
        />
      ) : null}
    </div>
  );
};
