# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 5 Nov 2026 (midday, Melbourne).*

- **Live:** server `418bca0` (`matches-20261105-v1`, 01:07 UTC 5 Nov).
  Released today under Mark's go-ahead (no activity): `idle-20261105-v1` (c56bc1a: #106, #107, #17, #109 stage A,
  idle loops #63), `batch-20261105-v3` (#93 background tag job, #86 hardening and USER 99:100, #110, #111, #96 items,
  #22, web #56/#97/#105), `matches-20261105-v1` (`/api/v1/server/matches` for #109 stage B). Rollback containers
  `PigMusic-before-<version>` kept.
- **Tag job (#93) done live:** Mark pressed Apply; 1,518 songs retagged/renamed in ~45 min, no errors, spot-checked.
  Originals stay in `.pig-music-trash/organisation/` until Mark cleans them.
- **Idle CPU (#63):** 5.7% of a core ~45 min after the release (7.3–7.8% before; the rig idles at 1%). Post-restart
  catch-up runs 10–13% for the first half hour. The rest is background passes in the worker threads; finding them
  needs a stack sampler on the live process (Mark's OK).
- **Mark's checks on build 20 (5 Nov):** Apple Music playlists in search work (MusicKit enabled; ShazamKit also
  enabled). Asked for next: add an Apple Music playlist as his own and download its songs (#121); Album editions
  misses the split Blink-182 albums (#120); Organise still lists ~1,500 songs after the tag job (#119).
- **iPhone/Watch:** **0.2.0 (20)** uploaded to TestFlight on 5 Nov: #115 songs retry a failed first connection and a
  stalled stream recovers or stops with a message (from Mark's 4 Nov diagnostics), #109 stage B Apple Music playlists
  in search (D-66; needs MusicKit ticked for the app identifier), #25, #112 Watch logo, three #97 iPhone/Watch fixes.
  Builds 18–19's changes and #114 still wait for Mark's device check.
- **T3 rig (#15) runs** (`scripts/test_rig.py`: check, artist, mixes, organise); 60-song library, too small for #22 timings.
- **Codex findings open:** #116, #117 (stream recovery edge cases), #118 (remastered titles miss the server's copy),
  #104, #101. Withdrawn in review and still to design: the Soulseek leftover sweep and the staged-audio sweep (#96).
- **Parked by Mark (31 Oct):** the four-mockup screen redesign and the brand audit.
- **Ways of working:** area batches, Codex rounds per D-59, Claude pushes (D-50); releases under D-52; D-58 closing.
- **Household details** live only in the gitignored `.private/` (D-45).

## Now

Batches by area (D-50); each ends with its Codex rounds (D-59), a push and, where it helps testing, a release (D-52).

1. **Library tools (server + web):** #119 Review lists only songs whose tags differ (and a shorter Undo list) ·
   #120 Album editions joins albums split across two editions. Two Codex rounds (file moves), a rig trial, release;
   then Mark joins *Dude Ranch* and *Enema of the State*.
2. **Apple Music playlists, server half:** #118 match remastered and featured-credit titles by `song_key` ·
   #121 `POST /api/v1/imports/apple-music`: an Apple Music playlist becomes the person's own playlist, missing songs
   download only when they confirm. Two Codex rounds (downloads, playlists); release.
3. **Native build 21 (iPhone/Watch):** #121 "Add to my playlists" on the Apple Music playlist screen (Add only / Add
   and download M songs) · #116, #117 stream recovery edge cases · #97 Download menu as one VoiceOver button · #104 ·
   #101. Two Codex rounds (playback); TestFlight (Mark types his Mac password, D-68).
4. **Mark on build 21 (T5):** add an Apple Music playlist and download its songs; then the older device checks below.

## Next (P1/P2)

- **Server load and live gates:** #63 the remaining ~5% idle CPU (needs a stack sampler on the live process: Mark's
  OK) · #22 Home mixes timing in the next device diagnostics, and a larger rig library to reproduce it · #17 lock hold
  count and #16/#20 the loaded window on the live server · #94 one live probe of remote "unavailable" time.
- **Speed Mark asked for:** #54 song search timing · #23 progressive remote relay (start cold remote songs sooner) ·
  #55 more Apple Music popular songs.
- **Device acceptance (Mark, T5):** #115 cold start on mobile data and #112 Watch logo (build 20) · #114 · #108, #62,
  #59, #56 (builds 18–19) · #78, #76, #65 · #95, one checklist of the former acceptance issues.

## Later (P3)

#96 the two withdrawn sweeps (Soulseek leftovers and staged audio, redesigned around the engine's transfer state and
publication claims), warming fairness, marker files, Trash retention · #97 web structure (Player, PersonalLibrary)
and test gaps · #86 pytest out of the image · #46 native split · clean the organisation trash once Mark is happy
(#93 step 5) · set-aside ideas (Mac app, Shazam, CarPlay, mix tuning, split DNS) live in
[docs/PRODUCT.md](docs/PRODUCT.md#set-aside), not in issues; Shazam stays set aside unless Mark brings it back
(ShazamKit is now enabled for the app identifier). Lower-severity security drafts are tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
