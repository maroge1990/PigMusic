# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 15 Nov 2026 (Melbourne), evening.*

- **Live:** release `madeforyou-20261115-v1`, server `d9c0df8` (D-99), applied 15 Nov, healthy. Batch 13 is
  iPhone/Watch/web only; its web half (#202 fix, D-101 copy) goes live with the next release.
- **TestFlight 0.2.0 (27):** uploaded 15 Nov (batches 10b and 11). **0.2.0 (28)** carries batch 13; staged on the Mac
  for Mark to upload.
- **Device round on build 27 (#184):** area A (build 27 fixes) passed with follow-ups; area B (similar songs, Rewind)
  passed on iPhone except tapping a Rewind song (#216) and web similar songs (#202, fixed in batch 13, not yet
  live); area C (Watch) passed with follow-ups #217-#220 (steps 3-4 untestable: slow transfers, #219); **area D
  (icons, larger text, Reduce Motion/Transparency, Increase Contrast, VoiceOver) not tested yet.** Results go into
  docs/DESIGN.md when the round ends.
- **Batch 13 'device round fixes' (#207 #209-#215 #202), pushed:** slower splash hand-off; one app-wide Undo bar above
  the mini player; finished requests show in library with download/Watch options; favourite star optimistic and never
  greyed by syncs (refusals roll back, unknown states re-read); Search Refresh removed; Remove from library as one
  action with Undo; Downloads pages in three sections (Transfers, On your phone/watch, Settings; Mark's choice) with
  swipe to cancel/remove and no descriptors; Home descriptors removed and "See all popular songs" (iPhone and web);
  web asks for similar songs as the last song starts. Codex round 1 (a high, a medium), round 2 (two mediums) and
  five confirmation rounds, the last approving.
- **Next:** area D on build 28; batch 14 = Watch background transfers (#219, Mark's pick, measure time and battery
  first), then #217/#218/#220, #208 (Watch status wording), #215 (helper-text keep/remove list for Mark), Rewind
  redesign Option A (#216, approved).
- **#188:** build 25's export: no transport failures while online (baseline 54 of 166), all HTTP/2; replies still
  take 1.5 s median (p90 3.1 s) on the path, not the server. Next: ask Mark whether HTTP/3 stays off, then suggest
  "Always use HTTPS".
- **YouTube trial (#193, D-91):** ends ~21 Nov; then the numbers on #193 and #191, a hand sample, and Mark decides
  keep or remove (next free decision number: D-103).
- **Retirement checks 28 Nov (#201)**, **#171** night-time `lifecycle_lock` holds, D-78 clean-up of old Unraid
  containers and `releases/native-20261114-v1`: unchanged from 14 Nov.
- **Ways of working:** area batches (D-50), Codex rounds per D-59 and D-71, Claude pushes and releases (D-99); D-58
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
