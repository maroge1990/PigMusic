# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 21 Nov 2026, morning (Melbourne).*

- **Live:** release `playlists-20261121-v1` (server `4bdf4a3`): batch F web findings, #268, search artist ids (#289),
  no-match diagnostics (#282; read after a few days with `scripts/performance_report.py --no-match`), no duplicate
  playlists (#288).
- **main `966f5f4`:** batch G merged (device-round fixes #284–#287, #290–#293, Shazam #124, #283, Watch wake-up rule and
  counters #219/D-124/D-126). Two Codex rounds and a confirmation (mediums: #300); compiled and tap-through tested.
  **TestFlight:** Xcode Cloud build started from branch `testflight-20261121-b1` on 21 Nov; delete the branch once the
  build (2003 or later) is on TestFlight. Mark then re-checks only the changed items. New sentence for him to OK:
  "Couldn't create <name>. Try again."
- **Reviewed and waiting their turn, each its own TestFlight build after G (branches are cut from the old
  `feature/batch-g`; merge main into each first):**
  - **Batch J, Watch complication (D-125):** `batch-j-complication` (pushed as `feature/batch-j`). Codex: two rounds and
    a confirmation, "no high finding blocks TestFlight"; mediums #305, #301. Compiled on GitHub up to `514e8a8`; the
    last two commits are not compiled yet. Whether the iPhone's push wakes the Watch app depends on the watchOS version:
    this build is the test (see #219). Mark may need to enable App Groups on the `.watchkitapp` identifier and register
    `au.com.pig.music.watchkitapp.complication` first. Words to OK: "N waiting" and the gallery description.
  - **Batch K, older iPhone findings:** `batch-k2` (#259, #260, #276, #277, #280, #281 with server field `added_at` and
    Add `outcomes`/`paused_ids`, #253). Codex: approved on the last confirmation. **Never compiled**: push as
    `feature/batch-k`. Has a server half, so a server release goes with it. #261 was withdrawn (kept on `batch-k`).
  - **Batch L, playback gap (#279):** `batch-l-279` (pushed as `feature/batch-l`). Codex: confirmed, mediums #303 and
    #306. Compiles and passes the PigKit suite on GitHub's Mac; its tap-through run was in progress.
- **Parked with the next design on the issue:** #266 (`batch-h-266-parked`), #270 (`batch-270-minimal-parked`), #261
  (`batch-k`).
- **Waiting on Mark, one at a time:** the wording above; #267 (what Play does on a catalogue card); #242 (queue limit
  numbers); the matching questions in #282 once live data is in; ShazamKit ticked for the app's identifier.
- **Decisions:** D-121 to D-126. Next: D-127.
- **Lessons:** the full PigKit suite does not build on Linux (builders use a scratch package of the pure sources, or
  a stub build for PigPlayback); the Linux Swift compiler is newer than Xcode's and accepts code Xcode rejects, so only
  GitHub's Mac settles "does it compile"; never chain verify and release in one background command (10-minute cut-off);
  Codex outputs of this session are in `.localdata/handover-20261121/`.

## Now

1. **Batch G on TestFlight:** confirm the build arrived, delete `testflight-20261121-b1`, Mark's re-check.
2. **Batches J, K, L to TestFlight in that order** (merge main in, compile, tap-through, build; K also a server release).
3. **Read the no-match diagnostics** (#282) after a few days; bring Mark the matching questions.
4. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
5. **Live checks that close with use:** #272, #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

#266, #270 and #261 from their new designs; findings #295–#306; #282's rule changes; #242 once Mark has chosen the
numbers; #267 once designed. Then #226, #231, #225.

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
