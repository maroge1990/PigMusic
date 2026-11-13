# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 13 Nov 2026 (late afternoon, Melbourne).*

- **Live:** release `web-20261114-v1`, server `a15d53c`, since 13 Nov about 16:05 Melbourne (D-87: releases at any
  time; Mark ran the apply step). It carries batches 'brand' (#179), 'idle' (#171, #172) and 'web' (#174, #175, #181,
  #182) on top of the artwork release. Healthy. Rollback: container `PigMusic-before-web-20261114-v1`, backup
  `pre-web-20261114-v1` (image `sha-8ee4c29`). D-78 clean-up of the older rollbacks (artwork 13 Nov, findings 9 Nov)
  waits on Mark's hands.
- **Live checks after this release:** the web changes are in the served bundle; #174, #175, #179, #181 and #182 are
  closed (D-58). The stall watcher thread now uses 0.02% of a core (#172). But idle CPU rose: the server used 3.10% of
  a core over 10 quiet minutes against the 0.97% baseline, spent in the asyncio worker threads with SQLite-like file
  work and nothing logged: **#190**, not yet explained (this batch, batch 4 or the morning's artwork release). Lock
  holds: only the start-up one in the first half hour; #171 waits for tonight's export and mix run to compare.
- **Parity plan (13 Nov evening):** after comparing Octo and Mornye, Mark chose to improve PigMusic towards their
  instant listening, downloads, artwork and "Made for you" (D-88), with instant sources asked together, JioSaavn first
  (D-89), and every replaced path retired (D-90). Issues #191–#205; order in **Now**.
- **Audit of 13 Nov:** report https://claude.ai/artifact/91KX6g4ciEB7npgbpR4aCH, handover
  https://claude.ai/artifact/C5NUCuD6V6v8EaYR2n5b7G, issues #168–#190. Batches 1 to 5 done. Next: #190, then batch 6
  agency and words (#183), 7 server dead code (#169, #170, #173), 8 YouTube and SoundCloud removal (#94), 9 iPhone dead
  code and search merge (#176–#178), 10 the review checklist (#184).
- **Pushed, not on a device yet:** batch 'iphone-brand' (#180), `0addcab`..`50c9099`: the splash ends when the saved
  library is shown; the pig with one line on empty and error screens (D-82) and a Library error state; pages say what
  is opening; plain words for sync and settings; "Couldn't download"; rounded figures where compared (D-84); a symbol
  per Watch state on song rows. Two Codex rounds, both approve with no findings. Icons are left for batch 10.
- **D-86:** Mark tests once, when all audit batches are done. The native batches (5, the native half of 6, 9, and the
  #188 app fix) go to TestFlight together as 0.2.0 (25) at the end; no build bump until then.
- **#188 (P1):** iPhone requests fail for 10–20 s after opening the app although the server answered. Cloudflare
  HTTP/3 switched off 13 Nov about 12:10 Melbourne as a test; compare Mark's next diagnostics export with the 13 Nov
  baseline (54 failed of 166 logged requests).
- **CI:** flakes in #189. The container check on `55ddf4c` failed on a racy web test (Your devices Loading), fixed in
  `e1b7623` (the test now answers the quality request).
- **iPhone/Watch:** latest TestFlight 0.2.0 (24); its items (#168) and build 23's (#149, #157–#163) wait for Mark's
  testing round (D-86).
- **Ways of working:** area batches (D-50), Codex rounds per D-59 and D-71, Claude pushes; releases at any time under
  D-87; D-58 closing. Household details live only in the gitignored `.private/` (D-45).

## Now: finish the audit and reach parity

Goal (D-88): instant listening, downloads, artwork and "Made for you" on par with Octo and Mornye, measured against
#191 (taps that play ≥ 90%; first sound median ≤ 1.5 s, p90 ≤ 3 s; mix downloads ≥ 80%; placeholders ≤ 2%). Every
batch that replaces something retires the old path (D-90, #201). Audit batches 1–5 are done; the rest are folded in
below. Server and web batches are released when ready (D-87); every native change waits for one TestFlight build and
Mark's single testing round (D-86).

| # | Batch | Issues | Platform | Codex | Ships in | Waits on |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Idle CPU regression | #190 | server | per fix | release | — |
| 2 | Measure: counters and a report for the targets | #191 | server, web admin | 1 | release; baseline posted on #191 | — |
| 3 | Look up the songs on screen before the tap | #192 | server | 2 | release | — |
| 4 | Sources (audit 8): SoundCloud out, Monochrome diagnosed, YouTube for instant listening as a one-week trial, FreeMp3Cloud and zvu4no out of instant (D-89) | #94, #193 | server | 2 | release, then the trial week | Mark's decision on #193 (revisits D-80) |
| 5 | Agency and words (audit 6): Undo over confirm | #183 | web (+ native half) | 2 | release; native half in the TestFlight build | — |
| 6 | Artwork: Deezer first for covers and artist photos (measure first) | #194 | server | 2 | release | — |
| 7 | Server dead code and duplicates (audit 7), with any retirement whose pass mark is met | #169, #170, #173 parts 1 and 3, #201 | server | 1 | release | Mark: keep or delete `server.admin` |
| 8 | Downloads: fake-lossless check, convert down from verified lossless, a readable log per download | #195, #196, #197 | server, web | 2 | release | Mark's decision on #196 (product rule) |
| 9 | Native (audit 9 + app fixes): dead code, search merge, Watch audio sharing, connection resilience, cover colours | #176, #177, #178, #188, #199 | iPhone, Watch | 2 | **TestFlight 0.2.0 (25)** | Mark approves #199's look from screenshots |
| 10 | Product review checklist and Mark's single testing round (D-86) | #184 | all | none | docs | Mark's devices |
| 11 | Made for you: keep playing similar songs, Rewind | #202, #203 | server, web, iPhone | 2 | release; native in TestFlight (26) | — |
| 12 | Retire what batches 4, 6 and 8 replaced, once their pass marks are met | #201 | server, web, iPhone | 2 | release; native in TestFlight (26) | the trials and two-week measurements |

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
