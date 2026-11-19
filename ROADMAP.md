# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 20 Nov 2026 (Melbourne).*

- **Live:** release `screens-20261119-v1` (server `91bc97d`): batch D web half, requests (#262–#264), robustness
  (#256–#258) and every web screen of D-120. main has changed only native code and tests since.
- **main `12d27d2`:** batch D iPhone half (#248, #249, #183), batch E iPhone (D-120 screens 1–15, 27–32) and Apple Watch
  (24–26, #252). Each passed two Codex rounds and confirmations. **TestFlight:** Xcode Cloud build from branch
  `testflight-20261120-b1` started 20 Nov; delete the branch once the build is on TestFlight. Not checked on a device.
- **#265:** done on our side; open until Mark installs a 2000+ build.
- **Known limits filed:** #279 (the shared playback engine restarts the current song at the same point when an upcoming
  song changes or is removed, iPhone and Watch), #278 (no covers on the Watch), #281 (iPhone Albums has no Recently added
  order), findings #266–#277 and #280.
- **Tap-through tests** on GitHub are timing-sensitive (slow runners, 45-minute job limit); a failure with an empty iPhone
  log is a runner problem: rerun. `ServerTests.testSendAnAlbumToTheWatch` now prints the text it found if it fails.
- **Waiting on Mark's device check:** the new TestFlight build (batch D and E on iPhone and Watch), then the older items
  (#168, #245, #239, #246, #229, #219, #232 to #235).
- **Decisions:** D-120 (19 Nov). Next: D-121.
- **Mac:** reachable as `ssh mac` (its address changes; match the host key); debugging only, about 14 GB free on 19 Nov.

## Now

1. **Mark checks on his phone** (build 1094, then 1135): the artwork (#168), batch B's notices (#245, #239), batch C's words and icons (#246), the song menu (#229), Watch background delivery
   with the phone locked (#219) then a diagnostics export the next day, #232 to #235, steps 17–18 of #184.
2. **YouTube trial ends ~21 Nov** (#193): keep or remove, Mark's decision.
3. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
4. **Live checks that close with use:** #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

Mark's device round on the D/E TestFlight build. Smaller: #253, #259 to #261, #242, findings #266 to #281, #279 (gapless
queue edits in the shared engine).

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
