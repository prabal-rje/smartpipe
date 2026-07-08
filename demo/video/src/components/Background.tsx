import React from "react";
import { AbsoluteFill } from "remotion";
import { COLORS } from "../config";

/**
 * The stage every scene plays on: near-black, a whisper of a dot grid,
 * two barely-there color glows, scanlines, and a vignette.
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
      {/* ambient color glows */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(820px 560px at 22% 18%, rgba(34,211,238,0.07), transparent 70%)," +
            "radial-gradient(900px 620px at 82% 78%, rgba(52,211,153,0.055), transparent 70%)",
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
