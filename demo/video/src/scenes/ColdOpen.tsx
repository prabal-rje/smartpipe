import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { COLORS, SCENE } from "../config";
import { MONO } from "../font";
import { SceneFrame } from "../components/SceneFrame";
import { Wordmark } from "../components/Wordmark";

/**
 * Scene 1 — cold open: a STATIC brand card (owner ruling, 2026-07-08).
 * Wordmark, tagline, and subtitle fade in together in 0.4 s — no draw-in,
 * no draw-head, no staggered entrances — hold perfectly still for ~4 s,
 * then the shared SceneFrame exit hands off to the Hook.
 */
export const ColdOpen: React.FC = () => {
  const frame = useCurrentFrame();

  // One fast fade for the whole card together (12 frames = 0.4 s).
  const cardOpacity = interpolate(frame, [0, 12], [0, 1], {
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
          opacity: cardOpacity,
        }}
      >
        <Wordmark drawStart={0} drawEnd={0} fontSize={36} />
        <div
          style={{
            fontFamily: MONO,
            fontSize: 40,
            fontWeight: 500,
            color: COLORS.text,
            letterSpacing: 0.4,
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
            marginTop: -22,
          }}
        >
          PDFs, images, audio, video, and text - verbs that understand.
        </div>
      </AbsoluteFill>
    </SceneFrame>
  );
};
