# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 16 Nov 2026 (Melbourne), late night.*

- **Live:** release `watchbg-20261116-v1`, server `762bc6b`, healthy. **TestFlight:** 0.2.0 (1034) from `762bc6b`.
- **Batch 24 (#219, D-113):** Watch copies prepared while the iPhone is locked (server prepares, background
  downloader, no polling), stopping after 2 h with nothing new on the Watch until the app is opened; diagnostics show
  background events and MetricKit battery figures. Codex: one high fixed and confirmed; medium filed as #231.
- **Batch 23 (#229, D-112):** song actions are a pop-up menu (⋯, long press, Now Playing). Medium filed as #230.
- **Batches 20–22** checked by Mark on 1021 and closed.
- **Decisions:** D-112 (song menu), D-113 (Watch background with a 2 h stop). Next: D-114.
- **Mac:** about 14 GB free; Mark's PigTV project shares the simulators and runs `simctl shutdown all`, which can kill
  our tap-through runs.

## Now

1. **Mark checks build 1034:** the song menu (#229); Watch background delivery with the phone locked (#219), then a
   diagnostics export the next day for the battery figures; steps 17–18 of #184.
2. **YouTube trial ends ~21 Nov** (#193): keep or remove, Mark's decision.
3. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
4. **Live checks that close with use:** #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

#226 (an unreadable download list or a source change must not empty the Watch), #230 and #231 (findings), #225 (test
workers' memory growth).

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
