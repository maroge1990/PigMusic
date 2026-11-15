# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 16 Nov 2026 (Melbourne), morning.*

- **Live:** release `scrim-20261116-v2`, server `d87fed8`, healthy (rollback container `PigMusic-before-scrim-20261116-v2`).
  **TestFlight:** 0.2.0 (970) from `d87fed8`, uploaded 16 Nov 09:04 by `scripts/testflight.sh` (cloud signing, build
  = commit count; 960 went up by hand earlier the same morning).
- **Built and waiting only for Mark's device round:** batches 13–17 (#202, #203, #206–#221, #223), plus older
  device checks (#116, #117, #123, #124, #149, #157–#163, #168, #176, #178, #180, #183). The round's 19 steps are on
  [#184](https://github.com/maroge1990/PigMusic/issues/184).
- **Batch 17 (16 Nov):** #186 brand check covers vendored files, #187 opaque scrim under Reduce Transparency, #189
  four CI flakes, #128 failed removals reported. Closed after checks. #125 has no cause left on current code: it waits
  for a repro on build 970.
- **Live log check (16 Nov):** #136, #137, #166, #167, #171, #172 confirmed and closed. Still open: #130 and #142 (not
  exercised: need two requesters / a connected device), #185 (needs a full day), #144 (no clear speed-up), #145 (the
  6-hourly artist re-warm half isn't done).
- **Decisions:** D-106 (the Download pop-up menu stays, despite iOS's unnamed inner button). Next: D-107.
- **Mac:** new home-network address (in `~/.ssh/config` on the Windows side); about 15 GB free.

## Now

1. **Mark's device round on build 970** (#184): the checklist on the issue. Its failures become the next batch.
2. **YouTube trial ends ~21 Nov** (#193): keep or remove, Mark's decision (D-107).
3. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met (taps that
   play ≥ 90%; first sound median ≤ 1.5 s, p90 ≤ 3 s; mix downloads ≥ 80%; placeholders ≤ 2%).
4. **Live checks that close with use:** #130 (two requesters at once), #142 (a device connected), #185 (a full day),
   #188 (Mark's diagnostics export after the round; HTTP/3 off since 13 Nov), #16/#17/#20/#22/#23/#54/#63 (#95).

## Next: small builds, one batch each

| Batch | Issues | Platform |
| --- | --- | --- |
| Server efficiency left over | #144 rebuild cost, #145 artist re-warm, #143 (measured: not worth it alone; close or fold in) | server |
| iPhone small features | #126 shorter Popular songs rows, #127 pause one download, #224 search flash, #112 Watch logo | iPhone, Watch |
| CI | #189 xdist workers dying in the 1 GB container | ops |

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
