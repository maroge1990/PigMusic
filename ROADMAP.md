# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 9 Nov 2026 (morning, Melbourne).*

- **Live:** release `audit-20261109-v1`, server `9c8e7e7`, since 8 Nov 15:33 Melbourne (D-73). Healthy; rollback
  container `PigMusic-before-audit-20261109-v1` and backup kept.
- **Live checks done (8 Nov night, 9 Nov after the mix-download run), numbers on each issue:**
  - Closed: #146 logs, #134 timing lines, #133 budgets, #141 library sync, #131 searches (0 engine errors in 445,
    38% with hits under 5 s), #132 queued peers (none past 40 s), #138 commit lock (0 holds in 57 commits), #135.
  - Overnight: 155 jobs, 57 acquired (median 43.5 s, max 117 s). With the saved 1-minute budget (D-77) 18 Soulseek
    transfers were cut off mid-download.
  - Idle CPU: our server 0.97% of a core (was 1.2%), slskd 2.41% (was 3.1%).
  - Still open: #136 and the new **#166** (export pass holds lifecycle_lock up to 10 s, about twice the baseline, and
    the warning can't name the step); **#167** (7 of 64 finished Soulseek transfers not found on disk); #130, #22,
    #142 not exercised (one requester, nobody on Home or connected); #63.
- **Next:** #166 and #167 (one server batch with #136), then #63's remaining timer loops and #145's artist re-warm.
- **iPhone/Watch:** 0.2.0 (23) on TestFlight since 8 Nov (#149, #157–#163, waiting on Mark's device check). Mark also
  owes a look at the web (#150–#156).
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
