# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 17 Nov 2026 (Melbourne), evening.*

- **Live:** release `stayup-20261117-v1`, server `933e5b1`, healthy. **TestFlight:** 0.2.0 (1041) from `5154afa`.
- **Ready to release, not released:** the artwork batch (#168), server `d71749b`, image
  `sha256:39903153…d5d2ce`. CI is green, Codex has approved (two rounds, one finding fixed and confirmed) and the
  Unraid verification and preflight passed as `artwork-20261117-v1`; only `deploy_release.py --apply` is left, then
  a TestFlight build for the iPhone half. The release fetches every artist photo again (about half an hour of
  background work); watch the log for `lifecycle_lock held` and `Server reports unavailable` meanwhile.
- **Artwork batch (#168):** server: a library song in a mix or Similar songs shows its album's current cover;
  Deezer's blank silhouette is refused and artist photos are fetched again (`portrait-v3`); Home warms what its
  cards ask for. iPhone: Apple rows in search ask for covers; a tile on screen keeps asking for up to about two
  minutes; a library album without a stored cover asks the server; Recently added follows the library; one full
  library resync after the update.
- **Batch A, stay up (live):** workers restart after an unexpected error (#236); a repeated request no longer holds
  the database for 13 to 15 s (#237); a busy database answers "try again" for reads only and anything unexpected
  answers a 500 that names its request (#239, server half; iPhone and web halves open); `/api/health/ready`, reachable
  only from inside the container, feeds the Docker health check and a watchdog that restarts a server that stops
  answering (#238). #236, #237 and #238 closed; left over in #256.
- **Review, 17 Nov:** server, web, iPhone and Watch reviewed; findings are #236 to #256. The server did not crash
  that morning (one 500, #235). `scripts/simulator_showcase.py` photographs every iPhone and Watch screen against a
  realistic test server (#254); what the pictures show is #255.
- **Waiting on Mark's device check:** #229 song menu, #219 Watch background delivery and battery figures, #232 to #235.
- **Decisions:** none new. Next: D-115.
- **Mac:** about 14 GB free; Mark's PigTV project shares the simulators and runs `simctl shutdown all`, which can kill
  our tap-through runs.

## Now

1. **Release the artwork batch** (`artwork-20261117-v1`), check it live, upload the TestFlight build, comment on #168.
2. **Mark checks on his phone:** the artwork (after that build), the song menu (#229), Watch background delivery
   with the phone locked (#219) then a diagnostics export the next day, #232 to #235, steps 17–18 of #184.
3. **YouTube trial ends ~21 Nov** (#193): keep or remove, Mark's decision.
4. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
5. **Live checks that close with use:** #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

From the 17 Nov review, in the suggested order (Mark confirms): **B, say what happened:** #241, #245, #244, #240,
and the iPhone and web halves of #239. **C, one vocabulary** (Mark chooses the words): #246. **D, one of each:**
#247, #248, #249, #183, #250. **E, the plain screens:** #251, #252, #209, #255. Smaller: #242, #243, #253, #256.

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
