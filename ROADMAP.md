# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 14 Nov 2026 (night, Melbourne).*

- **Live:** release `native-20261114-v2`, server `6ab9f7e`, applied by Claude 14 Nov ~20:20 Melbourne (D-99). Healthy,
  no errors. Carries everything through batch 9's two server fixes. Rollback: container
  `PigMusic-before-native-20261114-v2`, backup `pre-native-20261114-v2` (migration 13 since `deadcode-20261114-v1`).
  A failed first verify left an empty release folder `native-20261114-v1` on Unraid: include it in the D-78 clean-up.
  Nobody has used the app since 13 Nov 17:10, so #191's counters and #194's cover numbers are still empty.
- **Batch 9 'native' (#176, #177, #178, #188 app half, #199, #183 native half, #191 iPhone report), pushed
  `58c6949`..`128fc44`:** dead PigKit code out and probe tools in their own PigProbes target; one search pipeline
  (offline and the demo search the iPhone's library); one reading of audio-session events for phone and Watch, and the
  Watch bridge checks every 30 s instead of 2 s; about 15 s of retries for safe requests (now also favourites, listens
  and edits) and diagnostics naming the URLError and the connection (HTTP/3 or not); the iPhone reports first sound;
  the cover tint (D-96); swipe Remove, Hide and Cancel (and the Watch's Remove) act at once with Undo, haptics, inset
  row hairlines. Two server fixes found on the way and released: a stale native edit answers 409 (was 400), and
  members may report first sound (was owner-only). Two Codex rounds: round 1's two highs (deferred removal could hit
  the wrong song; Undo crossing accounts) and three follow-ups found in confirmations were fixed with failing-first
  tests; the last confirmation and round 2 approved. No finding issues are open.
- **TestFlight 0.2.0 (25):** batch 9 plus the native half of batch 5, on main at `128fc44` (native CI green). The
  upload waits for Mark to type his Mac password (`ssh -t mac /Users/markrogers/pigmusic-archives/testflight.sh`, ends
  with TESTFLIGHT-UPLOAD-OK). It is the build for Mark's single testing round (batch 10, D-86).
- **YouTube trial (#193, D-91):** started 14 Nov ~12:50, ends ~21 Nov. Then: the numbers on #193 and #191, a hand
  sample of wrong versions, and Mark decides keep or remove (D-100, #201).
- **#188 (P1):** the app half is in build 25. HTTP/3 off at Cloudflare since 13 Nov as a test; compare Mark's next
  diagnostics export with the baseline (54 failed of 166 logged requests); the new lines name the protocol. Then ask
  whether HTTP/3 stays off, and suggest "Always use HTTPS".
- **Retirement checks 28 Nov (#201):** Deezer artwork (fallback sources under 1%, placeholders at or under 2%) and
  convert-down (mix downloads at or over 80%). Act only if the pass mark is met.
- **#171** waits for a night's `lifecycle_lock` holds, measured at least an hour after a restart (the 14 Nov releases
  restarted it). **#190 closed.** **CI:** flakes in #189 (a ProgressiveSource timing test passed on rerun 14 Nov).
- **Earlier batches 1–8:** done and released by 14 Nov (details in the issues and git log); #169, #170, #190, #192,
  #94 closed.
- **Ways of working:** area batches (D-50), Codex rounds per D-59 and D-71, Claude pushes and releases (D-99); D-58
  closing. Household details live only in the gitignored `.private/` (D-45). The Mac is shared: check for other
  builds first; the tap-through script now shuts down only the simulators it booted; over SSH, FFmpeg needs
  `/opt/homebrew/bin` on PATH.

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
| 10 | **Next.** Product review checklist and Mark's single testing round on build 25 (D-86) | #184 | all | none | docs | Mark's devices |
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
