# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 5 Nov 2026 (morning, Melbourne).*

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

1. **Mark on build 20 (T5):** songs start after a cold launch on mobile data (#115); Apple Music playlists after
   ticking MusicKit (#109); Watch logo (#112); then builds 18–19's items, #114, #78, #76, #65, Home See all (#55), #95.
2. **#118** match remastered and featured-credit titles by `song_key` (server; quick, makes Apple playlists useful).
3. **#97 Download menu** as one VoiceOver button; **#116, #117** stream recovery edge cases (next native batch).
4. **#63** find the remaining ~5% idle CPU (stack sampler, with Mark's OK); re-measure #22 in the next diagnostics.

## Next (P1/P2)

- Reliability: #16/#20 loaded window on the live server · #96 the two sweeps, redesigned around the engine's transfer
  state and publication claims · a larger rig library for #22 · #104
- Mark's requests: #54 song search timing · #23 progressive relay
- Device acceptance (Mark, T5): #95, one checklist of the former acceptance issues

## Later (P3)

#96 remaining (warming fairness, marker files, Trash retention) · #97 web structure and test gaps · #86 pytest out of
the image · #46 native split · #101 · clean the organisation trash once Mark is happy (#93 step 5) · set-aside ideas
(Mac app, Shazam, CarPlay, mix tuning, split DNS) live in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside), not in issues.
Lower-severity security drafts are tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
