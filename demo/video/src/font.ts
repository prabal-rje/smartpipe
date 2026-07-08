import { loadFont } from "@remotion/google-fonts/JetBrainsMono";

const { fontFamily } = loadFont("normal", {
  weights: ["400", "500", "700", "800"],
  subsets: ["latin"],
});

/** The one typeface of the whole video. */
export const MONO = `${fontFamily}, "JetBrains Mono", Menlo, monospace`;
