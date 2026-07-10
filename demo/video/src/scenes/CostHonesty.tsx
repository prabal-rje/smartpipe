import React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS, SCENE, sec } from "../config";
import { MONO } from "../font";
import { Caption } from "../components/Caption";
import { SceneFrame } from "../components/SceneFrame";
import { Terminal, TerminalLine } from "../components/Terminal";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

const LINES: readonly TerminalLine[] = [
  {
    kind: "cmd",
    at: sec(0.4),
    text: `cat app.log | smartpipe where 'text has "ERROR"' | smartpipe filter "a real outage"`,
  },
  {
    kind: "note",
    at: sec(9.2),
    text: "note: run: ↑9.2k ↓312 tok",
  },
  {
    kind: "note",
    at: sec(10.0),
    text: "note: cache: 312 hits · 0 calls",
  },
];

const Pop: React.FC<{ at: number; children: React.ReactNode }> = ({
  at,
  children,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (frame < at) {
    return null;
  }
  const p = spring({
    frame: frame - at,
    fps,
    config: { damping: 13, mass: 0.6, stiffness: 140 },
    durationInFrames: 24,
  });
  const opacity = interpolate(frame - at, [0, 6], [0, 1], clamp);
  return (
    <div style={{ transform: `scale(${0.75 + 0.25 * p})`, opacity }}>
      {children}
    </div>
  );
};

/** A step chip: just the command name — no taxonomy tags (owner ruling:
 *  no "verb"/"free" jargon on screen; the receipt tells the cost story). */
const StepChip: React.FC<{ step: string }> = ({ step }) => (
  <div
    style={{
      padding: "16px 24px",
      borderRadius: 11,
      border: `1px solid ${COLORS.panelBorder}`,
      backgroundColor: "rgba(17,20,28,0.92)",
      fontFamily: MONO,
    }}
  >
    <span style={{ color: COLORS.cyan, fontSize: 30, fontWeight: 700 }}>
      {step}
    </span>
  </div>
);

const RollingCount: React.FC<{
  from: number;
  to: number;
  start: number;
  end: number;
  color: string;
  suffix?: string;
}> = ({ from, to, start, end, color, suffix }) => {
  const frame = useCurrentFrame();
  // Expo-like ease that ends at exactly 1, so the count lands on `to` dead-on.
  const value = Math.round(
    interpolate(frame, [start, end], [from, to], {
      ...clamp,
      easing: Easing.bezier(0.16, 1, 0.3, 1),
    }),
  );
  return (
    <div style={{ fontFamily: MONO, textAlign: "center" }}>
      <span style={{ fontSize: 52, fontWeight: 800, color }}>
        {value.toLocaleString("en-US")}
      </span>
      {suffix ? (
        <span style={{ fontSize: 26, color: COLORS.dim, marginLeft: 12 }}>
          {suffix}
        </span>
      ) : null}
    </div>
  );
};

const Arrow: React.FC = () => (
  <div style={{ fontFamily: MONO, fontSize: 42, color: COLORS.faint }}>→</div>
);

/** Scene 4 — cost honesty: cut the pile first, then read the receipt. */
export const CostHonesty: React.FC = () => {
  return (
    <SceneFrame duration={SCENE.cost}>
      <AbsoluteFill style={{ alignItems: "center" }}>
        <div style={{ marginTop: 120 }}>
          <Terminal
            title="~/ops — smartpipe"
            lines={LINES}
            width={1660}
            height={250}
            fontSize={24}
          />
        </div>
        {/* the funnel */}
        <div
          style={{
            marginTop: 96,
            display: "flex",
            alignItems: "center",
            gap: 34,
          }}
        >
          <Pop at={sec(3.4)}>
            <RollingCount
              from={0}
              to={50000}
              start={sec(3.4)}
              end={sec(4.4)}
              color={COLORS.text}
              suffix="lines"
            />
          </Pop>
          <Pop at={sec(3.9)}>
            <Arrow />
          </Pop>
          <Pop at={sec(4.1)}>
            <StepChip step="where" />
          </Pop>
          <Pop at={sec(4.6)}>
            <Arrow />
          </Pop>
          <Pop at={sec(4.8)}>
            <RollingCount
              from={50000}
              to={312}
              start={sec(4.8)}
              end={sec(6.2)}
              color={COLORS.cyan}
            />
          </Pop>
          <Pop at={sec(6.5)}>
            <Arrow />
          </Pop>
          <Pop at={sec(6.7)}>
            <StepChip step="filter" />
          </Pop>
          <Pop at={sec(7.2)}>
            <Arrow />
          </Pop>
          <Pop at={sec(7.4)}>
            <RollingCount
              from={312}
              to={17}
              start={sec(7.4)}
              end={sec(8.6)}
              color={COLORS.green}
            />
          </Pop>
        </div>
      </AbsoluteFill>
      <Caption
        at={sec(10.8)}
        text="Cut the pile down first. The model only reads what's left."
      />
    </SceneFrame>
  );
};
