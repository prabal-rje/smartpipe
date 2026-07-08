import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type SceneFrameProps = {
  /** Total frames of this scene — needed to time the exit. */
  duration: number;
  children: React.ReactNode;
  /** Skip the entrance (used by the cold open, which starts from black). */
  noEnter?: boolean;
  /** Skip the exit (used by the close, which fades to black itself). */
  noExit?: boolean;
};

/**
 * Camera-like scene shell: springs in from a slight zoom, drifts out on exit.
 * Wrap every scene in one of these and transitions stay consistent for free.
 */
export const SceneFrame: React.FC<SceneFrameProps> = ({
  duration,
  children,
  noEnter = false,
  noExit = false,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = noEnter
    ? 1
    : spring({ frame, fps, config: { damping: 200 }, durationInFrames: 20 });
  const enterOpacity = noEnter
    ? 1
    : interpolate(frame, [0, 12], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });

  const exitP = noExit
    ? 0
    : interpolate(frame, [duration - 14, duration - 2], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });

  const scale = (1.045 - 0.045 * enter) * (1 + 0.02 * exitP);
  const translateY = (1 - enter) * 22 - exitP * 10;
  const opacity = enterOpacity * (1 - exitP);

  return (
    <AbsoluteFill
      style={{
        transform: `scale(${scale}) translateY(${translateY}px)`,
        opacity,
      }}
    >
      {children}
    </AbsoluteFill>
  );
};
