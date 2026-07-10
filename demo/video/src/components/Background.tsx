import React from "react";
import { AbsoluteFill } from "remotion";
import { COLORS } from "../config";

/**
 * The stage every scene plays on: a FLAT near-black ground (owner ruling,
 * 2026-07-09: no glowy background gradient), a whisper of a dot grid,
 * scanlines, and a vignette.
 */
export const Background: React.FC = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      {/* dot grid */}
      <AbsoluteFill
        style={{
          backgroundImage:
            "radial-gradient(circle, rgba(228,228,231,0.055) 1px, transparent 1.4px)",
          backgroundSize: "28px 28px",
          backgroundPosition: "14px 14px",
        }}
      />
      {/* scanlines */}
      <AbsoluteFill
        style={{
          backgroundImage:
            "repeating-linear-gradient(0deg, rgba(0,0,0,0.16) 0px, rgba(0,0,0,0.16) 1px, transparent 1px, transparent 4px)",
          opacity: 0.35,
        }}
      />
      {/* vignette */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(ellipse at center, transparent 58%, rgba(0,0,0,0.42) 100%)",
        }}
      />
    </AbsoluteFill>
  );
};
