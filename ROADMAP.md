# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 16 Nov 2026 (Melbourne), night.*

- **Live:** release `again-20261116-v1`, server `8662317`, healthy. **TestFlight:** 0.2.0 (1021) from `8662317`.
- **Batch 22 (16 Nov), Mark's checks of 1012:** Watch songs waiting for the Watch can be paused (#228; held back, the
  manifest unchanged); a removed song can be requested again (#211, D-111: your own request lifts your own removal's
  block; a re-download never reuses the removed id). Codex rounds 1–2, three findings fixed and confirmed.
- **Batch 21 (16 Nov), Downloads pages (#228, D-109/D-110):** the server pauses and resumes one request (new status
  `paused`; asking again resumes it); This iPhone, Watch and Server pages group transfers by album, the mark on the
  right pauses, resumes or retries a song, and an album's mark does it for the whole album. Codex rounds 1–2, one
  medium fixed and confirmed. Waits for Mark's device check on 1012. Not verified: real taps (simulator downloads
  finish too fast).
- **Batch 20 (16 Nov), device-round failures:** #206, #207, #210, #211, #214, #227, in 998 and 1012. Waits for Mark's
  device check. Not measured: the first-launch cache migration's time on a large library.
- **Device round on build 985 (#184):** 13 steps pass; 17 (Shazam) and 18 (Reduce Transparency, Larger Text) on hold.
  Songs reach the Watch mainly with its app open (#219, D-51).
- **Decisions:** D-108 (removal hides everywhere until Undo runs out), D-109 (one design for the Downloads pages),
  D-110 (an album's mark acts on the whole album), D-111 (your request overrides your own removal's block). Next: D-112.
- **Mac:** about 15 GB free.

## Now

1. **Mark checks build 1021:** batch 20 (#206, #207, #210, #211, #214, #227) and batch 21 (#228), then steps 17 and
   18 of #184.
2. **YouTube trial ends ~21 Nov** (#193): keep or remove, Mark's decision.
3. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met (taps that
   play ≥ 90%; first sound median ≤ 1.5 s, p90 ≤ 3 s; mix downloads ≥ 80%; placeholders ≤ 2%).
4. **Live checks that close with use:** #130 (two requesters at once), #142 (a device connected), #185 (a full day),
   #188 (Mark's diagnostics export), #144/#145 (the warmer and rebuilds after batch 18), #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

#226 (an unreadable download list or a source change must not empty the Watch), #219 (locked delivery, after Mark
decides on D-51), #225 (test workers' memory growth).

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
