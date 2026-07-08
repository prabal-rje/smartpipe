import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS } from "../config";
import { MONO } from "../font";

type CaptionProps = {
  text: string;
  /** Frame (relative to the enclosing Sequence) when the card slides in. */
  at: number;
  accent?: string;
};

/** The caption card: slides up from the bottom edge, cyan bar at its left. */
export const Caption: React.FC<CaptionProps> = ({
  text,
  at,
  accent = COLORS.cyan,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (frame < at) {
    return null;
  }
  const p = spring({
    frame: frame - at,
    fps,
    config: { damping: 15, mass: 0.7, stiffness: 110 },
    durationInFrames: 28,
  });
  const opacity = interpolate(frame - at, [0, 10], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        position: "absolute",
        bottom: 76,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        transform: `translateY(${(1 - p) * 56}px)`,
        opacity,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 22,
          padding: "22px 38px 22px 30px",
          borderRadius: 12,
          border: `1px solid ${COLORS.panelBorder}`,
          backgroundColor: "rgba(17,20,28,0.92)",
          boxShadow: "0 18px 50px rgba(0,0,0,0.5)",
          fontFamily: MONO,
        }}
      >
        <div
          style={{
            width: 5,
            alignSelf: "stretch",
            borderRadius: 3,
            backgroundColor: accent,
            boxShadow: `0 0 14px ${accent}`,
          }}
        />
        <div style={{ color: COLORS.text, fontSize: 31, letterSpacing: 0.2 }}>
          {text}
        </div>
      </div>
    </div>
  );
};
