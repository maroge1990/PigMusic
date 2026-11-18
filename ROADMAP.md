# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 18 Nov 2026 (Melbourne), evening.*

- **Live:** release `words-20261118-v4`, server `3a51459` (batch C, released 12:50; healthy, readiness ok, no warnings).
  **TestFlight:** 0.2.0 (1135) from `ad7e50d`, with batch C's iPhone and Watch half.
- **Batch C, one word and one icon (#246, live, on TestFlight):** "Add to library" for asking the server for music (D-115),
  one icon per meaning (D-116) and one word per thing (D-117) on web, iPhone and Watch; table in docs/DESIGN.md, guarded
  by `tests/test_vocabulary.py`. Song rows and albums now report what the server did (Adding, Skipped). Reviews: Codex
  round per half, confirmations and a final round; its high (album Add resent paused songs) fixed and confirmed by an Opus
  stand-in review. Findings filed: #259 to #264.
- **#265 in progress (D-119, the Mac stops being the build machine):** the simulator tap-through tests run in CI
  (`native.yml` job `tap-through`, iPhone 17 + Apple Watch Series 11 on GitHub's iOS/watchOS 26.5; three mini-player
  tests skip below iOS 27). Xcode Cloud workflow "TestFlight" (Mark set it up 18 Nov) archives scheme PigMusic for
  internal TestFlight when a branch starting `testflight-` is pushed; build numbers start at 2000. Build 2000 failed on
  the OpenAPI plugin trust; `swift/ci_scripts/ci_post_clone.sh` fixes that and build 2001 was running at handover.
- **Codex:** usage ran out on 18 Nov and came back the same afternoon; an Opus stand-in review covered the gap.
- **Release check:** the Unraid verify's test suite now gets 2 GB (its tmpfs counts; both the 17 and 18 Nov suites peak
  at about 1.4 GB, #225).
- **Waiting on Mark's device check:** build 1094's artwork and notices (#168, #245, #239), then build 1135's words and
  icons (#246); #229 song menu, #219 Watch background delivery, #232 to #235.
- **Decisions:** D-115 to D-119 today. Next: D-120.
- **Mac:** reachable as `ssh mac` (its address changes; match the host key). PigMusic's build files are cleaned off after each use
  (D-119); about 25 GB free. Mark's PigTV project shares the simulators.

## Now

1. **Mark checks on his phone** (build 1094, then 1135): the artwork (#168), batch B's notices (#245, #239), batch C's words and icons (#246), the song menu (#229), Watch background delivery
   with the phone locked (#219) then a diagnostics export the next day, #232 to #235, steps 17–18 of #184.
2. **YouTube trial ends ~21 Nov** (#193): keep or remove, Mark's decision.
3. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
4. **Live checks that close with use:** #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

**First: #265** (tap-through tests on GitHub Actions, TestFlight on Xcode Cloud; D-119). Then, from the 17 Nov review:
**D, one of each** (designed, D-118): #248, #249, #183, #250 (#247 parked by Mark, 18 Nov). **E, the plain screens:** #251, #252, #209, #255. Smaller: #242, #253, #256, #257, #258, #259 to #264.

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
