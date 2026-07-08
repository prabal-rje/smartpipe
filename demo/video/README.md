# smartpipe demo video

A self-contained [Remotion](https://remotion.dev) project that renders the
~70-second smartpipe demo (1920x1080, 30 fps): six scenes - cold open, the
hook, multimodal, cost honesty, scale, close - all driven by one reusable
`<Terminal>` component and the frame-math constants in `src/config.ts`
(tune scene lengths there). Run `npm install` once, then `npm run studio`
to preview in the browser or `npm run render` to produce
`out/smartpipe-demo.mp4`; each scene is also registered as its own
composition (`ColdOpen`, `Hook`, `Multimodal`, `CostHonesty`, `Scale`,
`Close`), so `npx remotion render Hook out/hook.mp4` re-renders one scene
independently. The music bed is `public/silicon-prism-waltz.m4a` (60 s,
looped over the 70 s cut with a fade-out); if you ever need a
no-strings-attached replacement, `node scripts/make-music.mjs` (ffmpeg
required) synthesizes an original ambient track from scratch into
`public/music.m4a` - royalty-free by construction - and you can point the
`<Audio>` element in `src/Main.tsx` at it.
