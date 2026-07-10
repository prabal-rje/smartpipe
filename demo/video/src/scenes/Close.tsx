import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS, CPS, SCENE, sec } from "../config";
import { MONO } from "../font";
import { SceneFrame } from "../components/SceneFrame";
import { Terminal, TerminalLine } from "../components/Terminal";
import { Wordmark } from "../components/Wordmark";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

const INSTALL_LINES: readonly TerminalLine[] = [
  { kind: "cmd", at: sec(2.0), cps: CPS.slow, text: "brew install prabal-rje/tap/smartpipe" },
];

/** Scene 6 — close: the wordmark returns, the install line types, fade out. */
export const Close: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const markIn = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 26,
  });
  const urlOpacity = interpolate(frame, [sec(4.6), sec(5.2)], [0, 1], clamp);
  const fadeOut = interpolate(
    frame,
    [SCENE.close - 26, SCENE.close - 5],
    [0, 1],
    clamp,
  );

  return (
    <SceneFrame duration={SCENE.close} noExit>
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
          gap: 60,
        }}
      >
        <div
          style={{
            transform: `scale(${0.92 + 0.08 * markIn})`,
            opacity: interpolate(frame, [0, 12], [0, 1], clamp),
          }}
        >
          <Wordmark drawStart={0} drawEnd={0} fontSize={25} />
        </div>
        <Terminal
          title="install — smartpipe"
          lines={INSTALL_LINES}
          width={840}
          height={150}
          fontSize={27}
        />
        <div
          style={{
            fontFamily: MONO,
            fontSize: 26,
            color: COLORS.dim,
            letterSpacing: 0.6,
            opacity: urlOpacity,
          }}
        >
          github.com/
          <span style={{ color: COLORS.cyanDim }}>prabal-rje/smartpipe</span>
        </div>
      </AbsoluteFill>
      <AbsoluteFill style={{ backgroundColor: "#000", opacity: fadeOut }} />
    </SceneFrame>
  );
};
