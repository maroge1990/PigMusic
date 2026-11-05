# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 5 Nov 2026 (evening, Melbourne).*

- **Live:** server `d8029ca` (`tools-20261105-v1`): #122 prefix reads by index, mix-cache cleanup per edition (#63),
  #118 matching by `song_key`, #119 Review lists only real tag changes, #120 Album editions joins split albums (only
  where the kept release's tracklist confirms each song), #121 Apple Music import (server). Earlier today:
  `idle-20261105-v1`, `batch-20261105-v3` (#93 tag job ran live: 1,518 songs), `matches-20261105-v1`.
- **Pushed, not live:** journal ownership guard for publication and recovery (#96; Codex-confirmed per D-71).
- **iPhone/Watch:** **0.2.0 (21)** uploaded to TestFlight on 5 Nov: Shazam A (What's playing?), B (Shazamed inbox) and
  C (Share → PigMusic from the Shazam app; App Group set up by Mark), #121 Add to my playlists, #116, #117, #97
  Download button. Build 20's #115 and #112 wait for Mark's device check.
- **Idle CPU (#63):** 3.9% of a core after `tools-20261105-v1` (5.7% → 7.8% earlier); the server's own Python is
  ~7× quieter; the rest is mostly the embedded slskd. Small tails left: `Exporter.relative`, mix-cache cleanup.
- **Reviews (D-71, 5 Nov):** every fix for a Codex finding is confirmed by Codex before it ships; our own tests never
  stand in for it.
- **Mark's pending checks:** Album editions joins for *Dude Ranch* and *Enema of the State* (#120); Organise Review
  with tags (#119); build 21 on the phone.
- **Parked by Mark (31 Oct):** the four-mockup screen redesign and the brand audit.
- **Ways of working:** area batches (D-50), Codex rounds per D-59 and D-71, Claude pushes; releases under D-52;
  D-58 closing. Household details live only in the gitignored `.private/` (D-45).

## Now

Every open item is in this plan, in batches by area (D-50). Each batch ends with its Codex rounds (D-59), a push and,
where it helps testing, a release (D-52). Live checks run alongside from real use, and their findings join the next
batch.

1. **Server: efficiency and library tools.** #122 prefix reads by index (done locally: the remaining idle CPU, #63) ·
   the rest of #63 (`MixCache.retained`, re-measure after release) · #119 Review lists only real tag changes and a
   shorter Undo list · #120 Album editions joins split albums · #118 remastered and featured titles match. Two Codex
   rounds, rig trial, release; then Mark joins *Dude Ranch* and *Enema of the State*.
2. **Server: Apple Music import and speed.** #121 `POST /api/v1/imports/apple-music` (D-69: exact copy; downloads
   only on confirm) · #54 song search timing · #55 more Apple Music popular songs. Release.
3. **Native build 21 (iPhone/Watch).** #121 "Add to my playlists" · **Shazam** (options A + B below) · #116, #117
   stream recovery edge cases · #97 Download menu as one VoiceOver button · #104 · #101. TestFlight (D-68).
4. **Server: playback and clean-up.** #23 progressive remote relay (cold remote songs start before the whole file
   arrives) · #96 the two withdrawn sweeps redesigned around the engine's transfer state and publication claims,
   warming fairness, marker files, Trash retention (owner-run purge) · #86 pytest out of the image (a separate test
   layer for the release verify) · cleaning the organisation undo copies once Mark is happy (#93 step 5).
5. **Web.** #97 structure (Player, PersonalLibrary queue rules out into tested units) and its test gaps.
6. **Native structure.** #46 split AppModel and WatchPhoneController (no behaviour change; after build 21 settles).

**Shazam (S01/S02, back from set-aside; Mark's choice of option pending):** A: "What's playing?" in iPhone search
identifies a song with ShazamKit and shows it as in your library / on the server / not on the server, with Play, Add
and Download on confirm. B: a "Shazamed" inbox of identified songs with "Download all missing" on confirm (if
Apple's Shazam library includes songs found outside PigMusic, every Shazam shows up). C (only if B can't see them):
a share extension from the Shazam app. Recognising never downloads by itself.

## Live checks, running alongside

- **Nightly mix preparation** (≈13:00 UTC): freezes and lock holds (#16, #17, #20), export pass after #122.
- **Mark's next normal listening** (then send diagnostics): Home mixes timing (#22), search timing (#54), remote
  "unavailable" time (#94).
- **Idle CPU after the batch 1 release** (#63): py-spy sample in a quiet window; target well under the 5.7% now.

## Device checks (Mark, T5)

Build 20: #115 cold start on mobile data, #112 Watch logo · #114 · builds 18–19: #108, #62, #59, #56 iPhone · #78,
#76, #65 · #95 checklist. Each batch above adds its own steps.

## Set aside

Mac app, CarPlay, mix tuning and split DNS stay in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside). Lower-severity
security drafts are tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
