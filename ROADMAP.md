# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 8 Nov 2026 (morning, Melbourne).*

- **Live:** server `0b68d76` (unchanged since 5 Nov; checked 8 Nov, healthy). Nothing below is released yet.
- **Pushed, not released (audit of 6 Nov, #129–#163):**
  - Batch 0 `a91d67c`: native CI builds again on Xcode 26.6 (#129 closed). Two Swift timing tests fail on the CI
    runner (#165, Swift batch).
  - Batch 1 `7a0038c..3eb2f2c` downloads: one timing line per job and timings in the payload (#134), one length
    tolerance (#139 closed), Soulseek searches end at 3 good files and are deleted only once complete (#131), queued
    peers given up at 40 s and the per-source limit honoured (#132), 3 min a quality level and 6 a song (#133, D-72),
    fair claims between requesters (#130). Two Codex rounds.
  - Batch 2 `e57f32d..d8bc43e` per-song work: freemp3cloud paced for every caller (#140 closed), check() reuses the
    NAS check (#135), one tag save (#137), fair claims with one slot (#164 closed), the file moves before the write
    lock with crash recovery (#138), one debounced rebuild per album and no rebuild under the lifecycle lock (#136).
    Two Codex rounds plus confirmations.
  - Batch 3 idle/per-device: /library/changes without the lifecycle lock when unchanged (#141), Home mixes without
    per-song NAS lookups (#22), quieter logs (#146), event streams read only on change (#142), snapshot parsed once
    and artists in one query (#144), Home feeds kept a day (#145). #143 measured, unchanged.
- **Release next:** batch 1 alone (D-52) so its effect shows in #134's lines, then batches 2–3. Live numbers are owed on
  #130–#136, #138, #141, #22, #142, #146 before they close.
- **Not started:** batch 3's #63 idle items and #145's artist re-warm; batch 4 web (#154 done locally, not pushed;
  #150, #152, #151, #155, #153, #156); batch 5 iPhone/Watch (#157–#163, #149, #165).
- **iPhone/Watch:** 0.2.0 (22) on TestFlight (6 Nov).
- **Waiting on Mark:** #147, #148 and the two leftover `pig-music-test-*` containers (parked, one question each);
  device checks as before.
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
