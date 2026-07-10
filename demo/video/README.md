# smartpipe demo video

A self-contained [Remotion](https://remotion.dev) project that renders the
86-second smartpipe demo (1920x1080, 30 fps): eight scenes - cold open, the
hook, multimodal, cost honesty, index, search, graph, close - all driven by
one reusable `<Terminal>` component and the frame-math constants in
`src/config.ts` (tune scene lengths there). Run `npm install` once, then
`npm run studio` to preview in the browser or `npm run render` to produce
`out/smartpipe-demo.mp4`; each scene is also registered as its own
composition (`ColdOpen`, `Hook`, `Multimodal`, `CostHonesty`, `ScaleA`,
`ScaleB`, `Graph`, `Close`), so `npx remotion render Hook out/hook.mp4`
re-renders one scene independently. The narrated cut is the `MainNarrated`
composition: the script text lives in `src/narration/lines.json`, and
`RIME_API_KEY=... node scripts/fetch-narration.mjs` synthesizes the
voiceover wavs (Rime.AI, speaker cupola) into the gitignored
`public/narration/` and refreshes `src/narration/durations.json` - fetch
before rendering `MainNarrated`. The music bed is
`public/silicon-prism-waltz.m4a` (60 s, looped over the 86 s cut with a
fade-out, held far below the voice); if you ever need a
no-strings-attached replacement, `node scripts/make-music.mjs` (ffmpeg
required) synthesizes an original ambient track from scratch into
`public/music.m4a` - royalty-free by construction - and you can point the
`<Audio>` element in `src/Main.tsx` at it.
