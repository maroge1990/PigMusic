# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 16 Nov 2026 (Melbourne), late morning.*

- **Live:** release `warm-20261116-v1`, server `629079a`, healthy (rollback container `PigMusic-before-warm-20261116-v1`).
  **TestFlight:** 0.2.0 (979) from `629079a`, uploaded 16 Nov 10:11 by `scripts/testflight.sh` (cloud signing, build
  = commit count).
- **Built and waiting only for Mark's device round:** batches 13–18 (#126, #127, #202, #203, #206–#221, #223), plus
  older device checks (#112, #116, #117, #123, #124, #149, #157–#163, #168, #176, #178, #180, #183). The round's 22
  steps are on [#184](https://github.com/maroge1990/PigMusic/issues/184).
- **Batch 18 (16 Nov):** server #144 (rebuild artwork checks from one listing: 155 → 85 ms locally) and #145 (library
  artists warmed again only when changed or due), both live and open for their live checks; iPhone #224 (cancelled
  search keeps its results) closed; CI #189 (suite gets 3 GB, peak 1.7 GB measured) closed. Each Codex finding was
  fixed and confirmed. #126 and #127 turned out to be built on 5 Nov; they wait for the device round.
- **Batch 17 (16 Nov):** #128, #186, #187, #189 flakes; closed. #125 waits for a repro on a current build.
- **Live log check (16 Nov):** #136, #137, #166, #167, #171, #172 confirmed and closed. Still open: #130 and #142 (not
  exercised), #185 (needs a full day).
- **Decisions:** D-106 (the Download pop-up menu stays), D-107 (database keeps its safest saving mode; #143 closed).
  Next: D-108.
- **Mac:** new home-network address (in `~/.ssh/config` on the Windows side); about 16 GB free.

## Now

1. **Mark's device round on build 970** (#184): the checklist on the issue. Its failures become the next batch.
2. **YouTube trial ends ~21 Nov** (#193): keep or remove, Mark's decision (D-108).
3. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met (taps that
   play ≥ 90%; first sound median ≤ 1.5 s, p90 ≤ 3 s; mix downloads ≥ 80%; placeholders ≤ 2%).
4. **Live checks that close with use:** #130 (two requesters at once), #142 (a device connected), #185 (a full day),
   #188 (Mark's diagnostics export after the round; HTTP/3 off since 13 Nov), #144/#145 (the warmer and rebuilds after batch 18), #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

No build work is queued: the next batch comes from Mark's device round. Small findings left: #225 (test workers'
memory growth).

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
