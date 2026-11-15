# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 15 Nov 2026 (Melbourne), night: handover.*

- **Live:** release `rewind-20261115-v1`, server `cddad62` (D-102), applied 15 Nov, healthy (rollback container
  `PigMusic-before-rewind-20261115-v1`, backup `pre-rewind-20261115-v1`). Carries batches 13 and 15's server/web.
- **TestFlight:** 0.2.0 (28) uploaded 15 Nov (batch 13). Build 29 was never uploaded. **0.2.0 (30)** (batches 13, 14
  and 15) is staged in `~/pigmusic-build` on the Mac for Mark to upload (`testflight.sh`, log in
  `~/pigmusic-archives/upload.log`); clear the archive and `ArchiveIntermediates` afterwards.
- **Device round (#184):** on build 27, areas A-C done (results on #184; follow-ups #208-#221). **Still to test on
  build 30:** area D (icons in all appearances, larger text, Reduce Motion/Transparency, Increase Contrast,
  VoiceOver); batch 13 on device (Undo bar, favourite star, requested → in library, Remove from library, Downloads
  pages); Rewind (#216); the Watch delivery measurement (#219: send ~20 songs, close both apps 15 min, export
  diagnostics, read its "Watch delivery" section). Results go into docs/DESIGN.md when the round ends.
- **Pushed and awaiting Mark's check:** batch 13 'device round fixes' (#207 #209-#215 #202), batch 14 'Watch
  background transfers' (#219; gap #223: no resume after iOS evicts the app), batch 15 'Rewind Option A' (#216).
  Each passed its Codex rounds per D-59/D-71.
- **Next, in order:** #223; then the Watch issues in Mark's order #217 (Send to Watch in the app's menu style, no
  pop-ups; show Mark a layout first), #218 (delete the iPhone's Watch copy after delivery), #220 (phone Now Playing
  on the Watch; design with Mark), #208 (Watch status only when action is needed; wording with Mark); #215 (give Mark
  the keep/remove list of helper text, D-101); #221, #222 (findings).
- **#188:** build 25's export: no transport failures while online (baseline 54 of 166), all HTTP/2; replies still
  take 1.5 s median (p90 3.1 s) on the path, not the server. Next: ask Mark whether HTTP/3 stays off, then suggest
  "Always use HTTPS".
- **YouTube trial (#193, D-91):** ends ~21 Nov; then the numbers on #193 and #191, a hand sample, and Mark decides
  keep or remove (next free decision number: D-103).
- **Retirement checks 28 Nov (#201)**, **#171** night-time `lifecycle_lock` holds, D-78 clean-up of old Unraid
  containers (now also `PigMusic-before-batch13-20261115-v1` and `PigMusic-before-rewind-20261115-v1`) and
  `releases/native-20261114-v1`: unchanged from 14 Nov.
- **Ways of working:** area batches (D-50), Codex rounds per D-59 and D-71, Claude pushes and releases without waiting (D-99, D-102); D-58
  closing. The Mac is shared and near its disk floor (15 GB free): clear PigMusic's own archive and
  `ArchiveIntermediates` after an upload.

## Now: finish the audit and reach parity

Goal (D-88): instant listening, downloads, artwork and "Made for you" on par with Octo and Mornye, measured against
#191 (taps that play ≥ 90%; first sound median ≤ 1.5 s, p90 ≤ 3 s; mix downloads ≥ 80%; placeholders ≤ 2%). Every
batch that replaces something retires the old path (D-90, #201). Audit batches 1–5 are done; the rest are folded in
below. Server and web batches are released when ready (D-87); every native change waits for one TestFlight build and
Mark's single testing round (D-86).

| # | Batch | Issues | Platform | Codex | Ships in | Waits on |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | ~~Idle CPU regression~~ (closed 13 Nov: post-restart catch-up, no regression) | #190 | server | — | — | — |
| 2 | Measure: counters and a report for the targets (pushed 13 Nov) | #191 | server, web admin | 2 | release; baseline posted on #191 | — |
| 3 | ~~Look up the songs on screen before the tap~~ (released 14 Nov, closed) | #192 | server | 2 | release | — |
| 4 | Sources (audit 8, released 14 Nov; #94 closed, YouTube trial to ~21 Nov): SoundCloud out, Monochrome diagnosed, YouTube for instant listening as a one-week trial (D-91, D-95), FreeMp3Cloud and zvu4no out of instant (D-89) | #94, #193 | server | 2 | release, then the trial week | — |
| 5 | Agency and words (audit 6): Undo over confirm (web released 14 Nov; native half in batch 9) | #183 | web (+ native half) | 2 | release; native half in the TestFlight build | — |
| 6 | Artwork: Deezer first for covers and artist photos (released 14 Nov; placeholder rate after use) | #194 | server | 2 | release | — |
| 7 | ~~Server dead code and duplicates (audit 7)~~ (released 14 Nov) | #169, #170, #173 parts 1 and 3 | server | 2 | release | — |
| 8 | ~~Downloads: fake-lossless check, convert down from verified lossless, a readable log per download~~ (released 14 Nov, D-98) | #195, #196, #197 | server, web | 2 | release | — |
| 9 | ~~Native (audit 9 + app fixes): dead code, search merge, Watch audio sharing, connection resilience, cover colours, native Undo, first-sound report~~ (pushed 14 Nov, TestFlight 0.2.0 (25)) | #176, #177, #178, #188, #199, #183, #191 | iPhone, Watch | 2 | TestFlight 0.2.0 (25) | — |
| 10 | **In progress.** Product review checklist and Mark's single testing round on build 25 (D-86); its fixes are batch 10b (#199, #206, #207) in TestFlight (26) | #184 | all | none (10b: 2) | docs; TestFlight (26) | Mark's areas 2 and 4 |
| 11 | ~~Made for you: keep playing similar songs, Rewind~~ (pushed 15 Nov) | #202, #203 | server, web, iPhone | 2 | release; native in TestFlight (27) | — |
| 12 | Retire what batches 4, 6 and 8 replaced, once their pass marks are met | #201 | server, web, iPhone | 2 | release; native in a later TestFlight | the trials and two-week measurements |

Later: #204 concerts on artist pages (Mark picks a provider and area) · #205 Music Haptics (if Apple's framework
allows a third-party player) · #198 Mornye-Python as a lossless source (only if Monochrome fails; Mark's decision on
the risk) · #200 wrong-song check against the YouTube audio (only if #193 is kept) · moving covers · #173 part 2
shared move journal (design for Mark first).

Alongside: #171 tonight's lifecycle_lock holds; #188 Mark's next diagnostics export after the HTTP/3 test (HTTP/3 off
since 13 Nov ~12:10 Melbourne; then decide whether it stays off, and turn on "Always use HTTPS"); live checks from
real use (#16/#17/#20, #22, #54, #23, #63); #123 deferred Watch publish (native, rides in batch 9).

## Later (P3)

#96 the two library marker files (needs the live export config) · #46 remaining (full AccountSession, NativeService
per domain) · clean the organisation undo copies once Mark is happy (#93 step 5) · set-aside ideas (Mac app, CarPlay,
mix tuning, split DNS) live in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside). Lower-severity security drafts are
tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
