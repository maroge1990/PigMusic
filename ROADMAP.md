# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 8 Nov 2026 (afternoon, Melbourne).*

- **Live:** server `0b68d76` (unchanged since 5 Nov). Nothing below is released yet.
- **Pushed, not released (audit of 6 Nov, #129–#163):**
  - Batch 0 `a91d67c`: native CI builds on Xcode 26.6 (#129 closed).
  - Batch 1 `7a0038c..3eb2f2c` downloads: timing lines (#134), one length tolerance (#139 closed), Soulseek
    searches and queued peers (#131, #132), 3 min a quality level (#133, D-72), fair claims (#130). Two Codex rounds.
  - Batch 2 `e57f32d..d8bc43e` per-song work (#140 closed, #135, #137, #164 closed, #138, #136). Two Codex rounds.
  - Batch 3 idle/per-device (#141, #22, #146, #142, #144, part of #145; #143 measured, unchanged).
  - Batch 4 web `9224aa4..9cd9ad5`: PageHeader/SectionHeader everywhere (#150), one button file and focus ring
    (#152), one section heading and panel (#151), shared loading/empty/error (#155), spacing/type/shadow scales (#153),
    one home per selector and dead CSS gone (#156); #154 earlier (`5e2a51b`). Two Codex rounds plus a confirmation.
  - Batch 5a Swift `d1e9f16..69bb4bf`: CI timing races in two Swift tests (#165), diagnostics export (#149), Watch
    settings (#163). One Codex round plus a confirmation.
  - CI fixes: two order/layout bugs in the #146 tests (`72d1e48`, `6a73cf3`).
- **In progress:** batch 5b iPhone consistency, branch `claude/batch5b-iphone` (#161 done locally); #157, #158,
  #159, #160, #162 to do, then the simulator tap-through and two Codex rounds.
- **Release next:** batch 1 alone (D-52), then batches 2–4. Live numbers owed on #130–#136, #138, #141, #22, #142,
  #146; #150–#156 need a look in the real app; #149/#163/#165 need the next TestFlight build and CI.
- **Measure first:** #63's remaining timer loops and #145's artist re-warm, after batch 3 is live.
- **iPhone/Watch:** 0.2.0 (22) on TestFlight (6 Nov).
- **Waiting on Mark:** #147, #148 and the two leftover `pig-music-test-*` containers (parked, one question each).
- **Ways of working:** area batches (D-50), Codex rounds per D-59 and D-71, Claude pushes; releases under D-52;
  D-58 closing. Household details live only in the gitignored `.private/` (D-45).

## Now

1. **Mark on build 21 (T5)**, then send diagnostics: Shazam (identify, Shazamed, Share from the Shazam app), Add to my
   playlists, a few songs not on the server (#23 timing, #94), Home a few times (#22), searches (#54).
2. **#123** deferred Watch publish never rescheduled after an account change (native; build 22 with any device findings).
3. **Live checks from real use:** #16/#17/#20 freezes and lock holds (nightly mix run + Mark's listening), #22, #54,
   #94, #23 first-sound p90; #63 quiet-night idle measurement.
4. **Rig:** a larger generated library for #22 timings; #96 warming fairness; check the engine sweep against the real
   slskd layout.

## Later (P3)

#96 the two library marker files (needs the live export config) · #46 remaining (full AccountSession, NativeService
per domain) · clean the organisation undo copies once Mark is happy (#93 step 5) · set-aside ideas (Mac app, CarPlay,
mix tuning, split DNS) live in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside). Lower-severity security drafts are
tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
