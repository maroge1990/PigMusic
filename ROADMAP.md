# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 1 Nov 2026 (afternoon, Melbourne).*

- **Live:** server `03b44b5` (`search-20261031-v1`; rollback container `PigMusic-before-search-20261031-v1`).
  Overnight 31 Oct→1 Nov: 255 mix searches in ~2.5 h (was ~700 in ~7 h, #90 closed), 0 event-loop stalls, 146 export
  lock holds of 1–2.5 s (#17).
- **Pushed, not live** (main `4b17b88`): #106 one album download, one edition (D-64); #107 popular songs within ~2 s;
  #17 published-song check outside the lifecycle lock; #109 stage A Home mixes in search. Each with two Codex rounds;
  findings left: #110, #111 (P3).
- **iPhone/Watch:** native **0.2.0 (18)** uploaded to TestFlight on 1 Nov (#108 scrolling names, #62 one album page,
  #59, #107, #56 tap-to-play mixes, #109 mixes in search, four #97 fixes incl. offline sign-out per D-55). Build 17 is
  on Mark's phone. Not seen in a simulator (the Mac can't run one).
- **Closed on 1 Nov** after Mark's device checks: #98, #99, #100, #53, #64, #70, #71, #77, #19, #18, #60, #61, #39.
  Closed on live checks: #90, #91.
- **Still to check on devices (Mark):** #78, #76, #65; build 18's changes; Home See all (#55); the #95 checklist.
- **Codex findings open:** #104, #105, #101, #110, #111.
- **Parked by Mark (31 Oct):** the four-mockup screen redesign and the brand audit.
- **Ways of working:** area batches, Codex rounds per D-59, Claude pushes (D-50); releases under D-52; D-58 closing.
- **Household details** live only in the gitignored `.private/` (D-45).

## Now

1. **Release** the server batches (#106, #107, #17, #109) under D-52 tonight once CI is green and nobody is playing;
   then Mark runs **Tidy** on *Enema of the State* and *Dude Ranch*.
2. **Live gates after the release:** #17 hold count and named steps, #63 quiet idle CPU, #94 remote "unavailable"
   time, #16/#20 loaded window.
3. **Mark on build 18:** the scrolling names, album pages, tap-to-play mixes, mixes in search; then #78, #76, #65.
4. **Web half of #56** (tap-to-play mixes on the web) and the rest of #97.
5. **Waiting on Mark:** go-ahead on Apple Music playlists (#109 stage B needs a MusicKit key); when to run the tag job
   (#93); setting up the D-63 build keychain so uploads stop needing his Mac password.

## Next (P1/P2)

- Reliability: #15 T3 rig first full Unraid run, then #22 Home mixes latency on it · #25 harness attribution · #105
  first chart build vs web polling · #104
- Mark's requests: #109 stage B (Apple Music playlists) · #54 song search timing · #23 progressive relay
- Device acceptance (Mark, T5): #95, one checklist of the former acceptance issues

## Later (P3)

#96 smaller server findings (event loop, warming fairness, resilience, Soulseek imports) · #97 smaller web, iPhone
and Watch findings · #86 hardening · #46 native split · #93 apply the 1,475 consistent-tag changes safely · set-aside
ideas (Mac app, Shazam, CarPlay, mix tuning, split DNS) live in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside), not in
issues. Lower-severity security drafts are tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
