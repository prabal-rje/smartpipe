import React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS, SCENE, WIDTH, HEIGHT, sec } from "../config";
import { MONO } from "../font";
import { Caption } from "../components/Caption";
import { SceneFrame } from "../components/SceneFrame";
import { Terminal, TerminalLine } from "../components/Terminal";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

const LINES: readonly TerminalLine[] = [
  {
    kind: "cmd",
    at: sec(0.4),
    text: "smartpipe graph --fast 'project/*'",
  },
  { kind: "note", at: sec(2.9), text: "note: 2 entity names folded into 1 node" },
  {
    kind: "note",
    at: sec(3.4),
    text: "note: graph: 11 entities (2 folded) · 30 edges (0 pruned) · 0 tok",
  },
];

/** A named entity node: position is its center, `at` is when it pops in. */
type GraphNode = { label: string; x: number; y: number; at: number };

/** Default entity set (person, organization, location) — every label is a
 *  person, a company, or a place a stranger parses instantly. */
const NODES: readonly GraphNode[] = [
  { label: "Priya Sharma", x: 960, y: 480, at: sec(4.0) },
  { label: "Elena Vasquez", x: 565, y: 600, at: sec(4.4) },
  { label: "Northwind Ltd", x: 1355, y: 610, at: sec(4.8) },
  { label: "Marcus Webb", x: 740, y: 810, at: sec(5.2) },
  { label: "Berlin office", x: 1195, y: 830, at: sec(5.6) },
];

/** The unnamed remainder of the 11 entities — small, dim, background dots. */
type GraphDot = { x: number; y: number; at: number };

const DOTS: readonly GraphDot[] = [
  { x: 400, y: 520, at: sec(6.2) },
  { x: 700, y: 445, at: sec(6.35) },
  { x: 455, y: 775, at: sec(6.5) },
  { x: 1245, y: 450, at: sec(6.65) },
  { x: 1520, y: 480, at: sec(6.8) },
  { x: 1635, y: 735, at: sec(7.1) },
];

/** Edges reference node/dot centers; `bold` = between named entities. */
type GraphEdge = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  at: number;
  bold: boolean;
};

const between = (a: GraphNode, b: GraphNode, at: number): GraphEdge => ({
  x1: a.x,
  y1: a.y,
  x2: b.x,
  y2: b.y,
  at,
  bold: true,
});

const toDot = (a: GraphNode, d: GraphDot, at: number): GraphEdge => ({
  x1: a.x,
  y1: a.y,
  x2: d.x,
  y2: d.y,
  at,
  bold: false,
});

const [PRIYA, ELENA, NORTHWIND, MARCUS, BERLIN] = NODES;

const EDGES: readonly GraphEdge[] = [
  between(PRIYA, ELENA, sec(4.9)),
  between(PRIYA, NORTHWIND, sec(5.3)),
  between(ELENA, MARCUS, sec(5.7)),
  between(MARCUS, BERLIN, sec(6.1)),
  between(NORTHWIND, BERLIN, sec(6.4)),
  between(ELENA, NORTHWIND, sec(6.7)),
  toDot(PRIYA, DOTS[1], sec(6.9)),
  toDot(PRIYA, DOTS[3], sec(7.0)),
  toDot(ELENA, DOTS[0], sec(7.1)),
  toDot(MARCUS, DOTS[2], sec(7.2)),
  toDot(NORTHWIND, DOTS[4], sec(7.3)),
  toDot(BERLIN, DOTS[5], sec(7.4)),
];

/** One thin edge, drawn tip-to-tail once both endpoints exist. */
const EdgeLine: React.FC<{ edge: GraphEdge }> = ({ edge }) => {
  const frame = useCurrentFrame();
  if (frame < edge.at) {
    return null;
  }
  const t = interpolate(frame, [edge.at, edge.at + sec(0.5)], [0, 1], {
    ...clamp,
    easing: Easing.out(Easing.cubic),
  });
  return (
    <line
      x1={edge.x1}
      y1={edge.y1}
      x2={edge.x2}
      y2={edge.y2}
      stroke={edge.bold ? COLORS.cyanDim : COLORS.ghost}
      strokeWidth={edge.bold ? 2 : 1.5}
      strokeOpacity={(edge.bold ? 0.45 : 0.4) * t}
      pathLength={1}
      strokeDasharray="1"
      strokeDashoffset={1 - t}
    />
  );
};

/** A named entity chip, in the record-chip vocabulary of the other scenes. */
const NodeChip: React.FC<{ node: GraphNode }> = ({ node }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (frame < node.at) {
    return null;
  }
  const p = spring({
    frame: frame - node.at,
    fps,
    config: { damping: 13, mass: 0.6, stiffness: 140 },
    durationInFrames: 24,
  });
  const opacity = interpolate(frame - node.at, [0, 6], [0, 1], clamp);
  return (
    <div
      style={{
        position: "absolute",
        left: node.x,
        top: node.y,
        transform: `translate(-50%, -50%) scale(${0.75 + 0.25 * p})`,
        opacity,
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 20px",
        borderRadius: 10,
        border: `1px solid ${COLORS.panelBorder}`,
        backgroundColor: "rgba(17,20,28,0.92)",
        fontFamily: MONO,
        fontSize: 24,
        color: COLORS.text,
        whiteSpace: "pre",
      }}
    >
      <div
        style={{
          width: 10,
          height: 10,
          borderRadius: "50%",
          backgroundColor: COLORS.cyan,
        }}
      />
      {node.label}
    </div>
  );
};

/** One of the unnamed entities: a small dim dot fading in. */
const DotNode: React.FC<{ dot: GraphDot }> = ({ dot }) => {
  const frame = useCurrentFrame();
  if (frame < dot.at) {
    return null;
  }
  const opacity = interpolate(frame - dot.at, [0, 10], [0, 0.75], clamp);
  return (
    <div
      style={{
        position: "absolute",
        left: dot.x - 5,
        top: dot.y - 5,
        width: 10,
        height: 10,
        borderRadius: "50%",
        backgroundColor: COLORS.ghost,
        opacity,
      }}
    />
  );
};

/** Scene 6 — graph: entities and edges out of a folder, zero model calls. */
export const Graph: React.FC = () => {
  return (
    <SceneFrame duration={SCENE.graph}>
      <AbsoluteFill style={{ alignItems: "center" }}>
        <div style={{ marginTop: 100 }}>
          <Terminal
            title="~/project — smartpipe"
            lines={LINES}
            width={1660}
            height={250}
            fontSize={24}
          />
        </div>
      </AbsoluteFill>
      {/* the reveal: edges underneath, chips and dots on top */}
      <AbsoluteFill>
        <svg width={WIDTH} height={HEIGHT} viewBox={`0 0 ${WIDTH} ${HEIGHT}`}>
          {EDGES.map((e, i) => (
            <EdgeLine key={i} edge={e} />
          ))}
        </svg>
      </AbsoluteFill>
      <AbsoluteFill>
        {DOTS.map((d, i) => (
          <DotNode key={i} dot={d} />
        ))}
        {NODES.map((n) => (
          <NodeChip key={n.label} node={n} />
        ))}
      </AbsoluteFill>
      <Caption
        at={sec(10.6)}
        text="A knowledge graph from your documents, audio, and video."
      />
    </SceneFrame>
  );
};
