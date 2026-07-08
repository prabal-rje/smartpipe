import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS, SCENE, sec } from "../config";
import { MONO } from "../font";
import { SceneFrame } from "../components/SceneFrame";
import { Wordmark } from "../components/Wordmark";

const T = {
  drawStart: sec(0.6),
  drawEnd: sec(3.2),
  tagline: sec(3.6),
  subline: sec(4.9),
} as const;

/** Scene 1 — cold open: the wordmark draws itself out of the dark. */
export const ColdOpen: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const taglineP = spring({
    frame: frame - T.tagline,
    fps,
    config: { damping: 200 },
    durationInFrames: 24,
  });
  const taglineOpacity = interpolate(frame, [T.tagline, T.tagline + 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const sublineOpacity = interpolate(frame, [T.subline, T.subline + 16], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <SceneFrame duration={SCENE.coldOpen} noEnter>
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
          gap: 54,
        }}
      >
        <Wordmark drawStart={T.drawStart} drawEnd={T.drawEnd} fontSize={52} />
        <div
          style={{
            fontFamily: MONO,
            fontSize: 40,
            fontWeight: 500,
            color: COLORS.text,
            letterSpacing: 0.4,
            opacity: taglineOpacity,
            transform: `translateY(${(1 - taglineP) * 26}px)`,
          }}
        >
          Semantic pipes for your terminal.
        </div>
        <div
          style={{
            fontFamily: MONO,
            fontSize: 25,
            color: COLORS.dim,
            letterSpacing: 0.3,
            opacity: sublineOpacity,
            marginTop: -22,
          }}
        >
          PDFs, images, audio, video, and text - verbs that understand.
        </div>
      </AbsoluteFill>
    </SceneFrame>
  );
};
