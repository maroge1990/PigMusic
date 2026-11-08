# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 8 Nov 2026 (afternoon, Melbourne).*

- **Live:** server `0b68d76` (unchanged since 5 Nov). Release of everything below planned for 00:07 on 9 Nov (D-73);
  image `sha-9c8e7e7` passed the Unraid verification.
- **Pushed (audit of 6 Nov, #129–#165), all issues commented:**
  - Server: batch 1 downloads (#130–#134, #139 closed), batch 2 per-song work (#135–#138, #140/#164 closed), batch 3
    idle/per-device (#141, #22, #142, #144, #146, part of #145), rotating log file (#148, D-75).
  - Web, batch 4: #150–#156.
  - Swift, batch 5a: #165 closed, #149, #163; batch 5b: #157–#162 (iPhone consistency).
- **Owed after the release:** live numbers for #130–#136, #138, #141, #22, #142, #146; a look at the web (#150–#156);
  the next TestFlight build for #149, #157–#163; the #147 leftover files deleted during the release (D-74).
- **Measure first:** #63's remaining timer loops and #145's artist re-warm, once batch 3 is live.
- **iPhone/Watch:** 0.2.0 (22) on TestFlight (6 Nov); nothing newer uploaded.
- **Ways of working:** area batches (D-50), Codex rounds per D-59 and D-71, Claude pushes; releases under D-52/D-73;
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
