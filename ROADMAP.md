# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 14 Nov 2026 (late afternoon, Melbourne).*

- **Live:** release `sources-20261114-v1`, server `dad65a5`, applied by Mark 14 Nov ~12:50 Melbourne. Healthy; carries
  batches 'measure' (#191), 'lookahead' (#192) and 'sources' (#94, #193). YouTube works from the server (search
  1.05 s, address 2.8 s). Rollback: container `PigMusic-before-sources-20261114-v1`, backup
  `pre-sources-20261114-v1`. Six older rollback containers wait for the D-78 clean-up (Mark's hands). Counters for
  #191 started at the measure release (14 Nov 06:52): first numbers after a few days.
- **YouTube trial (#193, D-91):** started 14 Nov ~12:50, ends ~21 Nov. Then: the numbers on #193 and #191, a hand
  sample of wrong versions, and Mark decides keep or remove (#201).
- **Mark away (D-94):** Claude keeps pushing reviewed server and web batches and prepares each release (CI, verify,
  preflight); Mark applies the newest prepared release when back. Decisions taken before he left: YouTube trial
  (D-91), convert down from verified lossless if the numbers help (D-92), keep and document `server.admin` (D-93),
  yt-dlp updates itself for the trial (D-95), #199's tint stays out of TestFlight until he approves screenshots
  (built early and approved 14 Nov on branch `claude/cover-tint-199-6af23e`, D-96; it joins batch 9).
- **Batch 3 'lookahead' (#192), pushed `f0bee7d`..`47ef935`:** JioSaavn matches kept 6 h and re-checked on the tap;
  release pages, Home and settled searches look up their first songs ahead of the tap. Look-ahead never asks the
  relays or starts a transfer, has its own JioSaavn budget, and a tap overtakes a look-ahead still waiting. Two Codex
  rounds; one high and four mediums fixed and confirmed (D-71).
- **Batch 4 'sources' (#94, #193), pushed `f64577f`..`e770e93`:** SoundCloud out with its uploader review (#94;
  Monochrome is never searched because the saved profile is MP3 320 and it offers only lossless, useful once
  convert-down lands, #196). YouTube for instant listening on a one-week trial (D-91): asked with JioSaavn, which
  wins; FreeMp3Cloud and zvu4no out of instant listening; bytes passed through the server; yt-dlp updates itself
  (D-95) only to a checksum-matched release that runs in the worker, and a broken one is set aside for the pinned
  one. Admin, System lists each instant source's taps and answers for the trial. Two Codex rounds; two highs and
  three mediums fixed and confirmed. The trial week starts when its release is applied; Mark decides keep or remove
  after it (#193, #201).
- **Batch 5 'agency' (#183, web half), pushed `a3ae43c`..`933686b`:** destructive actions act at once with Undo
  (D-79): Remove from server and duplicates restore from Trash, Tidy runs its own undo, Organisation starts at once
  with per-change Undo, a playlist delete and Cancel all wait 10 s for Undo (sent at once if the page closes).
  Dialogs stay for empty Trash, sign out everywhere, reset or disable a person, replace the Apple review. Migration
  13 gives each queue entry an incarnation, so Cancel all clears only what was shown. Two Codex rounds; one high per
  round and five mediums fixed and confirmed. The native half (iPhone swipe Undo, Watch Remove, haptics, dividers)
  moves to batch 9.
- **Release `deadcode-20261114-v1` prepared for Mark's apply** (image `sha-a3a1c25`, verify and preflight passed 14 Nov
  ~16:08). It carries batches 5 'agency', 6 'artwork' and 7 'dead code' and supersedes `agency-20261114-v1` and
  `artwork-20261114-v1`, neither applied. Migration 13 (queue incarnations) comes with it: rolling back past it needs
  the backup `pre-deadcode-20261114-v1`. Nobody has used the app since 13 Nov 17:10, so #191's counters are empty.
- **Batch 6 'artwork' (#194), pushed `7887886`..`887a2dd`:** measured first (on #194): Deezer-first gives a cover to
  70 of 77 catalogue songs in active mixes (today 43) and a photo to 165 of 181 library artists (today 101). Deezer
  is now first for catalogue covers (barcode, MusicBrainz link, else same artist and title with version words kept)
  and artist photos (MusicBrainz link, else a unique name on a complete page), then the Cover Art Archive, Fanart
  and ArtistGrid. Each saved image counts its source for #201's pass mark. Also fixed: Deezer's track search
  answers nothing to `artist:"…" track:"…"`, so Monochrome's ISRC lookup never found one; it asks in free text.
  Two Codex rounds; four mediums fixed and confirmed.
- **Batch 7 'dead code' (#169, #170, #173 parts 1 and 3), pushed `c1d6b63`..`0b23b1c`:** unused vendor and server code
  removed (about 150 lines); `server.admin` documented in OPERATIONS.md and tested (D-93); one image save helper
  (`server/images.py`), per-key locks that are forgotten when idle (`server/keyed_locks.py`), one Fanart request
  budget, discover's own title rule replaced by the shared one (bare years kept), queue states named once
  (`server/queue_states.py`), the Apple import moved to `server/apple_export.py`. Not done, by choice: one cached-fetch
  helper for Fanart, Home and MusicBrainz (they differ in priority, outage and 404 handling), the YouTube remnants
  (YouTube is back for the trial). No #201 retirement has met its pass mark yet. Two Codex rounds; one medium fixed
  and confirmed.
- **#190 closed:** no regression. Measure idle at least an hour after a restart (server 0.55–0.84% of a core, engine
  2.64%, about 4 h after a restart). #171 still waits for a night's lock holds (the 14 Nov release restarted it).
- **Parity plan:** D-88 to D-90; issues #191–#205; order in **Now**. Audit of 13 Nov: report
  https://claude.ai/artifact/91KX6g4ciEB7npgbpR4aCH, handover https://claude.ai/artifact/C5NUCuD6V6v8EaYR2n5b7G.
- **Pushed, not on a device yet:** batch 'iphone-brand' (#180), `0addcab`..`50c9099`. Under D-86 the native batches
  go to TestFlight together as 0.2.0 (25) at the end; no build bump until then.
- **#188 (P1):** iPhone requests fail for 10–20 s after opening the app. Cloudflare HTTP/3 off since 13 Nov ~12:10
  Melbourne as a test; compare Mark's next diagnostics export with the baseline (54 failed of 166 logged requests).
- **CI:** flakes in #189. **iPhone/Watch:** TestFlight 0.2.0 (24); #168 and build 23's items wait for Mark (D-86).
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
| 1 | ~~Idle CPU regression~~ (closed 13 Nov: post-restart catch-up, no regression) | #190 | server | — | — | — |
| 2 | Measure: counters and a report for the targets (pushed 13 Nov) | #191 | server, web admin | 2 | release; baseline posted on #191 | — |
| 3 | ~~Look up the songs on screen before the tap~~ (released 14 Nov, closed) | #192 | server | 2 | release | — |
| 4 | Sources (audit 8, released 14 Nov; #94 closed, YouTube trial to ~21 Nov): SoundCloud out, Monochrome diagnosed, YouTube for instant listening as a one-week trial (D-91, D-95), FreeMp3Cloud and zvu4no out of instant (D-89) | #94, #193 | server | 2 | release, then the trial week | — |
| 5 | Agency and words (audit 6): Undo over confirm (web pushed 14 Nov; native half in batch 9) | #183 | web (+ native half) | 2 | release; native half in the TestFlight build | — |
| 6 | Artwork: Deezer first for covers and artist photos (pushed 14 Nov) | #194 | server | 2 | release | — |
| 7 | ~~Server dead code and duplicates (audit 7)~~ (pushed 14 Nov) | #169, #170, #173 parts 1 and 3 | server | 2 | release | — |
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
