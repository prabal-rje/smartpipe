import React from "react";
import { COLORS } from "../config";
import { MONO } from "../font";

export type FileKind = "pdf" | "png" | "mp3" | "mp4";

const Inner: React.FC<{ kind: FileKind }> = ({ kind }) => {
  const stroke = COLORS.cyanDim;
  switch (kind) {
    case "pdf":
      return (
        <>
          <line x1="16" y1="34" x2="48" y2="34" stroke={stroke} strokeWidth="2.5" />
          <line x1="16" y1="42" x2="48" y2="42" stroke={stroke} strokeWidth="2.5" />
          <line x1="16" y1="50" x2="38" y2="50" stroke={stroke} strokeWidth="2.5" />
        </>
      );
    case "png":
      return (
        <>
          <circle cx="24" cy="36" r="4" stroke={stroke} strokeWidth="2.5" fill="none" />
          <polyline
            points="14,54 26,44 34,50 46,38 50,42"
            stroke={stroke}
            strokeWidth="2.5"
            fill="none"
            strokeLinejoin="round"
          />
        </>
      );
    case "mp3":
      return (
        <>
          {[16, 23, 30, 37, 44].map((x, i) => {
            const h = [8, 16, 22, 14, 9][i];
            return (
              <line
                key={x}
                x1={x}
                y1={42 - h / 2}
                x2={x}
                y2={42 + h / 2}
                stroke={stroke}
                strokeWidth="3"
                strokeLinecap="round"
              />
            );
          })}
        </>
      );
    case "mp4":
      return (
        <polygon
          points="24,32 24,52 44,42"
          stroke={stroke}
          strokeWidth="2.5"
          fill="none"
          strokeLinejoin="round"
        />
      );
  }
};

/** A minimal line-art document badge: folded corner, format-specific mark. */
export const FileGlyph: React.FC<{ kind: FileKind; size?: number }> = ({
  kind,
  size = 92,
}) => {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 8,
      }}
    >
      <svg width={size} height={size * 1.1} viewBox="0 0 64 70" fill="none">
        {/* document outline with folded corner */}
        <path
          d="M10 4 H42 L54 16 V66 H10 Z"
          stroke={COLORS.dim}
          strokeWidth="2.5"
          fill="rgba(17,20,28,0.9)"
          strokeLinejoin="round"
        />
        <path
          d="M42 4 V16 H54"
          stroke={COLORS.dim}
          strokeWidth="2.5"
          fill="none"
          strokeLinejoin="round"
        />
        <Inner kind={kind} />
      </svg>
      <div
        style={{
          fontFamily: MONO,
          fontSize: 19,
          fontWeight: 700,
          letterSpacing: 2,
          color: COLORS.dim,
        }}
      >
        {kind.toUpperCase()}
      </div>
    </div>
  );
};
