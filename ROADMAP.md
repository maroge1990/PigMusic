# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 1 Nov 2026 (Melbourne).*

- **Live:** server `03b44b5` (`search-20261031-v1`, released 31 Oct 10:25 UTC; rollback container
  `PigMusic-before-search-20261031-v1`). Overnight 31 Oct→1 Nov: 255 mix searches in ~2.5 h (was ~700 in ~7 h, #90
  closed), 0 event-loop stalls, 146 export lock holds of 1–2.5 s (#17, the time is in the unnamed "rest" step).
- **iPhone/Watch:** native **0.2.0 (17)** from TestFlight on Mark's phone; his overnight test was very positive.
- **Closed on 1 Nov** after Mark's device checks: #98, #99, #100, #53 (search; his playlist note is #109), #64,
  #70, #71, #77, #19, #18, #60, #61, #39 (invite and lockout checks moved to #95). Closed on live checks: #90, #91.
- **New from Mark's testing:** #106 an album download split across two editions (cause found: MusicBrainz lists only
  25 releases per song; D-64 one album, one edition), #107 artist popular songs wait up to 10 s for album details,
  #108 one-line song and artist names that scroll (D-65), #109 playlists, mixes and Apple Music playlists in search,
  #62 re-scoped (the album *song list* differs between Library and artist/Home albums).
- **Still to check on devices (Mark):** #78 AirPods while paused, #76 changed downloads keep playback, #65 Pause stays
  paused; the #95 checklist.
- **Codex findings open:** #104, #105, #101 (fixed and shipped in build 17; close after a Mac test run).
- **Parked by Mark (31 Oct):** the four-mockup screen redesign and the brand audit.
- **Ways of working:** area batches, Codex rounds per D-59, Claude pushes (D-50); releases under D-52; D-58 closing.
- **Household details** live only in the gitignored `.private/` (D-45).

## Now (today, 1 Nov)

Mark's fresh reports first, then the server reliability tail. In order:

1. **Server batch "catalogue lookups"** (#106, #107 server part): one album request files on one edition, editions
   chosen from the release group's own track lists (not the capped 25-release list); popular songs never wait for
   album details. Library identity is on the risky list: two Codex rounds. Then Mark runs **Tidy** on *Enema of the
   State* and *Dude Ranch* to merge the existing halves.
2. **Server batch "export lock"** (#17): name the "rest" step and move it out of the lifecycle lock; one Codex round
   with batch 1's second round if small.
3. **Release** batches 1–2 under D-52 tonight once CI is green and nobody is playing; then the live gates: #63 quiet
   idle CPU after the night's mix work, #94 remote "unavailable" time, #17 hold count, #16/#20 loaded window.
4. **iPhone batch → build 18** (#108 scrolling one-line names, #62 one album screen for every album, #107 show songs as
   they arrive, #59 album songs delay, #56 tap-to-play smart playlists): Mac build, two Codex rounds (playback screen),
   TestFlight upload (first one with the D-63 build keychain).
5. **Search playlists, stage A** (#109): own playlists checked, Home mixes and smart playlists in search results.
6. **Waiting on Mark:** device checks #78, #76, #65, and Home's See all with up to 100 popular songs (#55); Tidy after batch 1; go-ahead on Apple Music playlists (#109 stage
   B needs a MusicKit key from the Apple Developer account); when to run the tag job (#93).

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
