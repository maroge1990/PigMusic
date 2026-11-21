# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 21 Nov 2026, early afternoon (Melbourne).*

- **Live:** release `iphonefix-20261121-v1` (server `b768c80`): everything in `playlists-20261121-v1` plus the server
  half of batch K (Add `outcomes` and `paused_ids`, albums' `added_at`, an Add whose copy has gone since it was matched
  is queued). The no-match diagnostics (#282) are collecting: read them after a few days with
  `scripts/performance_report.py --no-match`.
- **main `432d919`** holds four iPhone and Watch batches, each through two Codex rounds and confirmations, compiled on
  GitHub's macOS runner, and tap-through tested on the Mac on iOS 27.0 and watchOS 27.2 (25 iPhone, 7 Watch tests; the
  PigKit suite passes there too):
  - **G** device-round fixes #284–#287, #290–#293, Shazam #124, #283, Watch counters and wake-up rule (mediums: #300);
  - **L** queue edits that do not interrupt the playing song #279 (left: #303, #306);
  - **K** older iPhone findings #259, #260, #276, #277, #280, #281, #253 (approved; #261 withdrawn; left: #302, #304);
  - **J** the Watch complication, D-125 (left: #305, #301).
- **TestFlight (Xcode Cloud), four builds started 21 Nov, each adding to the last:** `testflight-20261121-b1` (G),
  `-b2` (G+L), `-b3` (G+L+K), `-b4` (all four, with the complication). Their status cannot be read from here: ask Mark
  which build numbers (2003 or later) arrived, then delete each branch. **b4 may fail to sign** (new identifier
  `au.com.pig.music.watchkitapp.widget` and the app group on the Watch app): if so Mark enables App Groups on the
  `.watchkitapp` identifier, registers the new identifier with App Groups, and b4 is pushed again as b5.
- **Mark's device checks, as builds arrive:** the changed items from his 20 Nov round; listening for #279; the two-day
  Watch delivery test with the complication on his active face (#219 has what the diagnostics should show; whether the
  iPhone's push wakes the Watch app depends on the watchOS version); Shazam (#124: it should now end in a result or Try
  again; the export shows where it stops).
- **Decided 21 Nov:** the three wordings (D-127), queue limits (D-128, #242: ready to build), the full menu on
  catalogue cards (D-129, #267: ready to build).
- **Parked with the next design on the issue:** #266 (`batch-h-266-parked`), #270 (`batch-270-minimal-parked`), #261
  (`batch-k`).
- **Waiting on Mark, one at a time:** #267 (what Play does on a catalogue card); #242 (queue limit numbers); the
  matching questions in #282 once live data is in; ShazamKit ticked for the app's identifier.
- **Decisions:** D-121 to D-129. Next: D-130.
- **Lessons:** the Mac (`ssh mac`, Mark's wish on 21 Nov when he is its only user) runs the tap-through tests in ~15
  minutes on the iOS his phone has; one run at a time, one folder per branch, and with its 8 GB a second booted
  simulator makes a run look hung. GitHub's runner is on iOS 26.5 and often times out or reports an empty iPhone log.
  The Linux Swift compiler is newer than Xcode's and accepts code Xcode rejects. Never chain verify and release in
  one background command. This session's Codex outputs are in `.localdata/handover-20261121/`.

## Now

1. **TestFlight:** confirm which builds arrived, delete their branches, fix signing for b4 if needed, Mark's checks.
2. **Read the no-match diagnostics** (#282) after a few days; bring Mark the matching questions.
3. **#266, #270, #261** from the designs on their issues, each its own batch.
4. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
5. **Live checks that close with use:** #272, #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

Findings #295–#306; #282's rule changes; #242 once Mark has chosen the numbers; #267 once designed. Then #226, #231,
#225.

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
