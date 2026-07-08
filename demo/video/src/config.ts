/**
 * Single source of truth for timing, palette, and type.
 * All scene lengths are frame-math constants — tune here, everything follows.
 */

export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;

/** Seconds → frames at the project frame rate. */
export const sec = (s: number): number => Math.round(s * FPS);

/** Scene durations, in frames. Order here is the order in the Main timeline. */
export const SCENE = {
  coldOpen: sec(5), // 1 — static brand card: one fast fade, ~4s hold, exit
  hook: sec(14), // 2 — map over invoices/*.pdf
  multimodal: sec(14), // 3 — files → pipe → records; audio commitments
  cost: sec(14), // 4 — where (free) → filter (paid) funnel + receipt
  scale: sec(12), // 5 — embed a folder of videos, top_k by meaning
  close: sec(8), // 6 — wordmark, install line, URL, fade
} as const;

export const TOTAL_FRAMES =
  SCENE.coldOpen +
  SCENE.hook +
  SCENE.multimodal +
  SCENE.cost +
  SCENE.scale +
  SCENE.close;

/** Terminal-native palette: near-black, cyan + green accents, zinc text. */
export const COLORS = {
  bg: "#0c0e12",
  panel: "#11141c",
  panelBorder: "#1e2430",
  titleBar: "#161a24",
  cyan: "#22d3ee",
  cyanDim: "#67e8f9",
  green: "#34d399",
  greenDim: "#6ee7b7",
  yellow: "#eab308", // stderr notes, dimmed
  text: "#e4e4e7", // zinc-200
  dim: "#a1a1aa", // zinc-400
  faint: "#5b6270", // muted punctuation / metadata
  ghost: "#3f4552",
} as const;

/** Typewriter speeds, characters per second. */
export const CPS = {
  command: 32,
  slow: 20,
} as const;
