# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 16 Nov 2026 (Melbourne), morning: batch 16 shipped to TestFlight.*

- **Live:** release `rewind-20261115-v1`, server `cddad62`, unchanged (batch 16 is native and scripts only).
  **TestFlight:** 0.2.0 (960) uploaded 16 Nov 07:35 from `4dd3909`; build numbers are now the commit count.
- **Batch 16 'Watch delivery and words', pushed to main (`4dd3909`), CI green:** #223 background restore for Watch
  copies, #217 Send to Apple Watch in the Download menu (D-103), #215 helper text removed, #218 delete the iPhone's
  Watch copy once delivered, #220 the iPhone's song on the Watch (D-104), #221 receipts read only against their
  manifest, #208 Watch messages only when Mark must act (D-105), #222 PigKit timing tests on a test clock. Two Codex
  rounds (one medium fixed and confirmed); simulator tap-through tests pass. Each issue has a comment; all wait for a
  device check.
- **D-106:** the Download button keeps its pop-up menu though iOS gives it a second, unnamed button VoiceOver can
  reach; the tap-through test no longer checks for it.
- **TestFlight is cloud-signed:** `ssh mac 'cd ~/pigmusic-release && git pull --ff-only && sh scripts/testflight.sh'`
  (settings in `~/.pigmusic-testflight.env`, Admin API key). The archive is unsigned, then ad hoc signed with each
  bundle's entitlements so the App Group survives the cloud signing (`d9fc7df`, one Codex round, approved). The
  script's full run has not been used since that fix; build 960 went up by hand with the same steps.
- **Mac:** its home-network address changed; `~/.ssh/config` on the Windows side points `mac` at the new one. It has
  about 15 GB free; the test copy's caches were cleared.
- **Watch row risk to check on device (#220):** tapping the iPhone row may open the Watch's own paused queue in
  Apple's Now Playing instead of the iPhone's.
- **Device round (#184) on hold** at Mark's request; areas D, batch 13, Rewind (#216), #219 and batch 16 to test on
  build 960. Next decision number: D-107 (YouTube keep/remove ~21 Nov, #193). Retirement checks 28 Nov (#201).

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
