# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 19 Nov 2026 (Melbourne).*

- **Live:** release `screens-20261119-v1`, server `91bc97d` (healthy at release). Earlier today: `oneofeach-20261119-v1`
  (batch D web half) and `steady-20261119-v1` (requests and robustness). **TestFlight:** 0.2.0 (1135) and Xcode Cloud
  build 2001 (480600a); the iPhone half of batch D is not on TestFlight yet.
- **#265 done on our side (D-119):** TestFlight builds come from Xcode Cloud when a `testflight-…` branch is pushed (build
  2001 reached TestFlight); tap-through tests run in CI; docs updated. Open until Mark installs a 2000+ build.
- **Batch D, one of each (D-118):** web half live (one song row, one menu, collection menus, mixes as pages, one notice
  area, one in-app confirm dialog; the server refuses a web write started under another sign-in, `X-Pig-Expected-Session`).
  iPhone half on `feature/batch-d-ios`, waiting for a green tap-through run, then two Codex rounds, then TestFlight.
- **Requests (#264, #262, #263) and robustness (#256, #257, #258):** live. An ordinary Add never resumes a song the
  asker paused (the Add answer has `paused`; the iPhone's "Paused. Resume it in Downloads." is in batch D's iPhone half).
- **Batch E, the plain and main screens (D-120, canvas "PigMusic plain screens", 35 screens):** web half live (16–23,
  33–35). iPhone (1–15, 27–32) and Watch (24–26, #252) start after batch D's iPhone half lands.
- **Findings filed today:** #266 to #275 (Codex mediums after last rounds). #242 (queue limits) left for later.
- **Waiting on Mark's device check:** build 1094/1135 items (#168, #245, #239, #246), #229, #219, #232 to #235.
- **Decisions:** D-120 today. Next: D-121.
- **Mac:** reachable as `ssh mac` (its address changes; match the host key); debugging only now. PigMusic's build files
  were cleared off it on 19 Nov.

## Now

1. **Mark checks on his phone** (build 1094, then 1135): the artwork (#168), batch B's notices (#245, #239), batch C's words and icons (#246), the song menu (#229), Watch background delivery
   with the phone locked (#219) then a diagnostics export the next day, #232 to #235, steps 17–18 of #184.
2. **YouTube trial ends ~21 Nov** (#193): keep or remove, Mark's decision.
3. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
4. **Live checks that close with use:** #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

**Finish D** (iPhone half: #248, #249, #183) and **E** (iPhone and Watch screens: #251, #252, #255; #209). Smaller: #253,
#259 to #261, #242, findings #266 to #275.

Then: #226 (an unreadable download list or a source change must not empty the Watch), #230 and #231 (findings), #225
(test workers' memory growth).

## Later

#247 album and artist pages that look the same wherever you open them (parked by Mark, 18 Nov) · #204 concerts on artist pages (Mark picks a provider and area) · #205 Music Haptics (if Apple allows a third-party
player) · #121 Apple Music playlists as your own, downloading the missing songs · #198 Mornye-Python as a lossless
source (only if Monochrome fails) · #200 wrong-song check against YouTube audio (only if #193 is kept) · #173 part 2
shared move journal (design for Mark first) · #96 the two library marker files · #46 remaining (AccountSession,
NativeService per domain) · clean the organisation undo copies once Mark is happy (#93 step 5) · set-aside ideas
(Mac app, CarPlay, mix tuning, split DNS) in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside). Lower-severity security
drafts are tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
