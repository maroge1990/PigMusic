# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 17 Nov 2026 (Melbourne), night.*

- **Live:** release `sayso-20261117-v1`, server `eb65d3d` (batch B, released 22:01; healthy, readiness ok). Before it,
  `artwork-20261117-v1` (20:36): no lock or unavailable warnings in the 35 minutes after; artist photos are being
  fetched again. **TestFlight:** 0.2.0 (1094) from `eb65d3d`, with the iPhone halves of the artwork batch and batch B.
- **Batch B, "say what happened" (live, on TestFlight):** #239 web and iPhone halves (a busy server, a save that may or
  may not have happened, and a server fault each say so; the iPhone drops a queued request only on the server's own
  500), #245 (a failed action shows a notice on every iPhone page, above the Undo bar), #240 (an unreadable Trash,
  organisation or tidy-up record is set aside instead of stopping start-up or breaking Trash, history and Undo), #241
  (the admin System page shows how the last run ended, recent errors, busy answers, workers and set-aside records;
  console log lines carry the time), #243 (Retry joins the song's newer job instead of making a second download) and
  #244 (request matrix, faults, and any unhandled server error now fails the test suite). Codex: a round per fix,
  every finding fixed and confirmed, plus a round over the batch. Findings filed: #257, #258.
- **Artwork batch (#168):** live and on TestFlight; waiting on Mark's device check.
- **Waiting on Mark's device check:** #168 artwork, #245 and #239 notices, #229 song menu, #219 Watch background delivery and battery figures,
  #232 to #235.
- **Next batch:** C, one vocabulary (#246), waiting on Mark's choice of words.
- **Decisions:** none new. Next: D-115.
- **Mac:** about 15 GB free; the TestFlight script needs 15 and refuses below it. PigMusic's own build caches can be
  cleared to make room. Mark's PigTV project shares the simulators.

## Now

1. **Mark checks on his phone** (build 1094): the artwork (#168), batch B's notices (#245, #239), the song menu (#229), Watch background delivery
   with the phone locked (#219) then a diagnostics export the next day, #232 to #235, steps 17–18 of #184.
3. **YouTube trial ends ~21 Nov** (#193): keep or remove, Mark's decision.
4. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
5. **Live checks that close with use:** #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

From the 17 Nov review, in the suggested order (Mark confirms): **C, one vocabulary** (Mark chooses the words): #246. **D, one of each:**
#247, #248, #249, #183, #250. **E, the plain screens:** #251, #252, #209, #255. Smaller: #242, #253, #256, #257, #258.

Then: #226 (an unreadable download list or a source change must not empty the Watch), #230 and #231 (findings), #225
(test workers' memory growth).

## Later

#204 concerts on artist pages (Mark picks a provider and area) · #205 Music Haptics (if Apple allows a third-party
player) · #121 Apple Music playlists as your own, downloading the missing songs · #198 Mornye-Python as a lossless
source (only if Monochrome fails) · #200 wrong-song check against YouTube audio (only if #193 is kept) · #173 part 2
shared move journal (design for Mark first) · #96 the two library marker files · #46 remaining (AccountSession,
NativeService per domain) · clean the organisation undo copies once Mark is happy (#93 step 5) · set-aside ideas
(Mac app, CarPlay, mix tuning, split DNS) in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside). Lower-severity security
drafts are tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
