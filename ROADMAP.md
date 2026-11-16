# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 17 Nov 2026 (Melbourne), morning.*

- **Live:** release `retry-20261117-v1`, server `5154afa`, healthy. **TestFlight:** 0.2.0 (1041) from `5154afa`.
- **Batch 25 (device round on 1034):** a song that fails while paused shows its failure and retry mark, its pause
  kept for automatic re-adds (#232); Remove all asks with a centred alert (#233, D-114); one Clear finished button,
  pink only when there is something to clear (#234); a failed song asked for again from a playlist no longer answers
  500, and ServerBusy is said in words (#235). Codex: one medium fixed and confirmed; nothing open.
- **Waiting on Mark's device check:** #229 song menu, #219 Watch background delivery and battery figures, #232–#235.
- **Decisions:** D-114 (Remove all alert). Next: D-115.
- **Review, 17 Nov:** polish and resilience review of server, web, iPhone and Watch filed as #236–#253. The server
  did not crash that morning (one 500, #235); the real gaps are workers that stop for good on one error (#236) and
  a stuck server nothing restarts (#238). Mark has the report and picks the order.
- **Mac:** about 15 GB free; Mark's PigTV project shares the simulators and runs `simctl shutdown all`, which can kill
  our tap-through runs.

## Now

1. **Mark checks build 1034:** the song menu (#229); Watch background delivery with the phone locked (#219), then a
   diagnostics export the next day for the battery figures; steps 17–18 of #184.
2. **YouTube trial ends ~21 Nov** (#193): keep or remove, Mark's decision.
3. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
4. **Live checks that close with use:** #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

From the 17 Nov review, in the suggested order (Mark confirms): **A, stay up:** #236, #238, #239, #237. **B, say what
happened:** #241, #245, #244, #240. **C, one vocabulary** (Mark chooses the words): #246. **D, one of each:** #247,
#248, #249, #183, #250. **E, the plain screens:** #251, #252, #209. Smaller: #242, #243, #253.

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
