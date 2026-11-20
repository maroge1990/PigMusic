# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 21 Nov 2026, early morning (Melbourne). The session stopped at a usage limit.*

- **Live:** release `playlists-20261121-v1` (server `4bdf4a3`): batch F web findings, #268, search artist ids (#289),
  no-match diagnostics (#282, read them after a few days with `scripts/performance_report.py --no-match`), no duplicate
  playlists (#288). All through Codex per D-59/D-71.
- **Batch G (iPhone and Watch), branch `feature/batch-g` (27 commits, `199e1b9`), NOT on main:** device-round fixes
  #284–#287, #290–#293, #124 (Shazam), #283, and the Watch wake-up rule and counters (#219, D-124, D-126). Two Codex
  rounds and a final confirmation: no high; mediums are #300. Both apps compile and PigKit tests pass on GitHub; the
  **tap-through run 44740297439 failed one test**, the new
  `DemoLibraryTests.testASwipeRemovesASongFromUpNextWithUndo` (line 485: "unable to perform Swipe left"), so the
  swipe-to-remove in the player (#287) is unproven: find out whether the test's row lookup or the List's swipe is at
  fault before merging. Then: merge to main, push, then
  `git push origin <commit>:refs/heads/testflight-YYYYMMDD-bN` (Xcode Cloud), delete that branch once the build arrives.
  New sentence for Mark to OK: "Couldn't create <name>. Try again."
- **Built, in Codex round 1, none compiled on Apple platforms yet, each to be its own TestFlight build after G:**
  - **Batch J, Watch complication (D-125):** branch `batch-j-complication` / `feature/batch-j` (compile run started).
    Codex round 1 output saved in `.localdata/handover-20261121/`. Mark may need to enable App Groups on the
    `.watchkitapp` identifier and register `…watchkitapp.complication` in the developer account (steps in the #219
    thread to be posted). Words to OK: "N waiting", the gallery description.
  - **Batch K, older iPhone findings:** branch `batch-k` (#259–#261, #276, #277, #280, #281 with a server field
    `added_at`, #253). Codex round 1 was running.
  - **Batch L, playback gap (#279):** branch `batch-l-279`. Codex round 1 was running. Tests run only on macOS CI.
- **Parked with the design on the issue:** #266 Undo for Remove from playlist (`batch-h-266-parked`; next: removal by
  entry key without a revision) and #270 saved listens across tabs (`batch-270-minimal-parked`).
- **Device round on build 2002 done 20 Nov;** #219's finding: the iPhone half works, the Watch takes songs only when
  its app runs. **YouTube stays** (D-121).
- **Waiting on Mark, one at a time:** #267 (what Play does on a catalogue card), #242 (queue limit numbers), the song
  matching questions in #282 once live data is in, the wording above.
- **Decisions:** D-121 to D-126. Next: D-127.
- **Lessons:** the full PigKit suite does not build on Linux (builders use a scratch package of the pure sources);
  never chain verify and release in one background command (10-minute cut-off); `tests/test_native_*` now run on
  GitHub once batch G lands (five fail on main until then).

## Now

1. **Batch G to TestFlight** (see above), then Mark re-checks only the changed items.
2. **Batches J, K, L:** read each Codex round 1, fix, round 2 and confirmation, compile on GitHub, one TestFlight
   build each (J first: it is what makes Watch delivery work).
3. **Read the no-match diagnostics** (#282) after a few days; bring Mark the matching questions.
4. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
5. **Live checks that close with use:** #272, #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

#266 and #270 (new designs on the issues); findings #294–#300, #282's rule changes, #242 once Mark has chosen the
numbers; #267 once designed. Then #226, #230 (mostly covered by batch G), #231, #225.

## Later

#247 album and artist pages that look the same wherever you open them (parked by Mark, 18 Nov) · #204 concerts on artist pages (Mark picks a provider and area) · #205 Music Haptics (if Apple allows a third-party
player) · #121 Apple Music playlists as your own, downloading the missing songs · #198 Mornye-Python as a lossless
source (only if Monochrome fails) · #200 wrong-song check against YouTube audio (YouTube is kept, D-121) · #173 part 2
shared move journal (design for Mark first) · #96 the two library marker files · #46 remaining (AccountSession,
NativeService per domain) · clean the organisation undo copies once Mark is happy (#93 step 5) · set-aside ideas
(Mac app, CarPlay, mix tuning, split DNS) in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside). Lower-severity security
drafts are tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
