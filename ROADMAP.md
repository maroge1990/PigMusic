# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 5 Nov 2026 (night, Melbourne).*

- **Live:** server `2f3e5b1` (`relay-20261105-v2`): batch 1 (#122, #118, #119, #120, #121 server, #63 cleanup) plus
  #23 progressive remote relay (only `bytes=0-1` completes before a file is validated), #96 engine leftover sweep by
  transfer state, staged-audio sweep by claim, owner-run Trash purge (descriptor-relative, restart-safe), #86 no pytest
  in the image, journal ownership guard. Six releases on 5 Nov in all, under Mark's go-ahead.
- **Pushed, not live (native):** #46 split of AppModel/WatchPhoneController (no behaviour change).
- **iPhone/Watch:** **0.2.0 (21)** on TestFlight (5 Nov): Shazam A/B/C, #121 Add to my playlists, #116, #117, #97.
- **Web:** #97 structure split (Player, PersonalLibrary) with queue unit tests and new Playwright coverage — live.
- **Idle CPU (#63):** 3.9% of a core (7.8% this morning); server Python ~7× quieter; the rest is mostly slskd.
- **Reviews (D-71):** every Codex finding's fix is confirmed by Codex before it ships. Today that took 3 passes for
  the journal guard, 4 for #23 and 5 for the Trash purge.
- **Mark checked live:** #119, #120 (Blink-182 albums joined), #109 B. **Waiting on Mark's devices:** build 21
  (Shazam, Add to my playlists), #115, #112, #114, #108, #62, #59, #56 iPhone, #78, #76, #65, #55 See all, #95.
- **Parked by Mark (31 Oct):** the four-mockup screen redesign and the brand audit.
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
