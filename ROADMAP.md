# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 8 Nov 2026 (night, Melbourne).*

- **Live:** release `audit-20261109-v1`, server `9c8e7e7`, since 8 Nov 15:33 Melbourne (D-73). Healthy; rollback
  container `PigMusic-before-audit-20261109-v1` and backup kept.
- **First live check (8 Nov, 22:17-23:00, 6¾ h after the release), numbers on each issue:**
  - Closed on live evidence: #146 (392 log lines in 6¾ h against 10,149 a day; no httpx or health lines, chart
    timeouts one line each), #134 (every job logs its stages), #133 (longest job 87 s, was up to 30 min), #141
    (`/library/changes` all under 1 s, was a 2.6 s median).
  - #131: no engine "Failed to finalize search" errors (was 1,763 a day); early stop not seen yet (no search had hits).
  - Idle CPU: our server 0.97% of a core (was 1.2%), slskd 2.41% (was 3.1%). #63 and #142 stay open.
  - Not exercised yet (no song acquired since the release): #130, #132, #135, #136, #138; #22 needs listing timed
    during preparation. Only lock holds over 1 s were at startup (1.3 s, 2.2 s in `recover_requests`).
  - The live download budget looks like 1 minute a quality level (the stage timings stop at 60 s), not the 3-minute
    default; one 9 MB transfer was cut off. A setting for Mark, not a code change.
- **Next:** the morning check of the overnight mix-download run (9 Nov) for the issues not yet exercised; then
  #63's remaining timer loops and #145's artist re-warm.
- **iPhone/Watch:** 0.2.0 (23) uploaded to TestFlight 8 Nov (#149, #157–#163, waiting on Mark's device check).
  Mark also owes a look at the web (#150–#156).
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
