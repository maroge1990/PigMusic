# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 15 Nov 2026 (Melbourne), later.*

- **Live:** release `native-20261114-v2`, server `6ab9f7e` (D-99), healthy. Rollback: container
  `PigMusic-before-native-20261114-v2`, backup `pre-native-20261114-v2`. No server change since.
- **TestFlight 0.2.0 (25):** uploaded 14 Nov 20:55 (the first run failed silently at signing; the script now runs
  with its output saved to `~/pigmusic-archives/upload.log` on the Mac).
- **Batch 10 (#184), Mark's device round on build 25, in progress:** area 1 (iPhone Undo, haptics, hairlines) passed
  except song requests (#206); area 2 (Watch: playlist update, Watch Remove, headphones out) **not tested yet**;
  area 3 passed (offline search #177; diagnostics #188) except the cover tint, too faint to see (D-100: 45%); area 4
  (icon appearances, larger text, Reduce Motion/Transparency, Increase Contrast, VoiceOver) sent to Mark, no
  result yet. Splash pig dropped at launch (#207). Results go into docs/DESIGN.md when the round ends.
- **Batch 10b 'device fixes' (#199, #206, #207), pushed `83c8718`..`5e0ff7d`:** tint at 45%; splash pig centred on the whole
  screen with a smoother hand-off; "Clear" in three toolbars; song requests act at once with Undo, show Requested
  and wait in a per-account request outbox (one song per request; a busy server keeps them, a refusal or a song
  excluded by the household drops them with a notice); Cancel my request with Undo. Codex round 1 (a high: 5xx
  dropped queued requests; a medium: Requested stuck after cancel) and round 2 (a medium: skipped songs marked
  Requested) and two confirmation findings (the notice cleared by a refresh) were fixed with tests; the last
  confirmation approved. Build 26 was not uploaded: Mark chose one TestFlight after batch 11 (build 27).
- **Batch 11 'Made for you' (#202, #203), pushed `8d52a1f`..`4cc75d9`:** similar songs when a queue ends (server
  `/radio/continuation`, keep_playing preference merged so older apps can't reset it; web and iPhone, "Similar songs"
  in Up next; iPhone fetches ahead and plays on) and Rewind (server `/rewind`, web `#/home/rewind`, iPhone Home ›
  Your Rewind). Migration 14 `listen_details` keeps what each listen was, so a year survives mix clean-up; a listen
  counts as the song's length (the apps report once at 30 s). Codex round 1 (a high and two mediums), round 2 (a
  medium) and the confirmation approved. Server and web: release next; iPhone: TestFlight (27) with batch 10b.
- **#188:** build 25's export: no transport failures while online (baseline 54 of 166), all HTTP/2; replies still
  take 1.5 s median (p90 3.1 s) on the path, not the server. Next: ask Mark whether HTTP/3 stays off, then suggest
  "Always use HTTPS".
- **YouTube trial (#193, D-91):** ends ~21 Nov; then the numbers on #193 and #191, a hand sample, and Mark decides
  keep or remove (next free decision number: D-101).
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
