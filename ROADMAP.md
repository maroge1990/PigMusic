# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 31 Oct 2026 (night, Melbourne).*

- **Live:** server `03b44b5` (released 31 Oct 10:25 UTC as `search-20261031-v1`, the second release that day with
  Mark's approval; rollback container `PigMusic-before-search-20261031-v1`). It carries every batch of 31 Oct: YouTube
  removed (#94), lock-hold steps (#17), export-off Trash (#96), the ranked search, Apple's lane and exact-version
  matching (#53, #102, #103), and up to 100 popular songs (#55).
- **iPhone:** native **0.2.0 (17)** uploaded to TestFlight on 31 Oct (new search screen, #101, #55 See all); build 16
  is on Mark's phone. Uploads run from the Mac (`~/pigmusic-archives/testflight.sh`, Mac password once per run until
  the separate build keychain of D-63 is set up).
- **Codex findings open:** #104 (fixed before push, no own round), #105 (cold chart build vs web polling).
- **Parked by Mark (31 Oct):** the four-mockup screen redesign and the brand audit.
- **Ways of working:** area batches, Codex rounds per D-59, Claude pushes (D-50); releases under D-52; D-54
  efficiency first; D-58 closing rule. Local suite: `pytest -n 4`, under a minute.
- **Live gates owed:** #90 first night's search count on `99b49eb`, #63 quiet idle CPU, #16/#20 loaded window, #91
  first nightly run; after the next release: #94 unavailable time, #17 lock-hold steps.
- **Household details** live only in the gitignored `.private/` (D-45).
- **Waiting on Mark:** installing build 16 from TestFlight (internal tester group) and checking #98–#100; the cellular
  checks on #39; when to run the tag job (#93); his device checks (#95, sections B–F of his list); the `owner` items.

## Now

Reliability first (D-47), with Mark's player reports and iPhone search. In order:

1. **iPhone player fixes** — build 16 on TestFlight; Mark installs it and checks #98–#100
2. **iPhone search** (#53, D-61) — stages 1–3 pushed; next: fix #103 (Apple live/remix versions must not open as the
   studio recording), then the iPhone results screen (Top results · Artists · Albums · Songs · Playlists, a mixed
   list while typing, Apple results marked not on the server), #102, #54 timing, #55 popular songs
3. **Live gates** — #90, #63, #16/#20 measurements; the first nightly scale-gate run (#91)
4. **Release the server batch** (#94, #17, #96) under D-52 once CI is green and nobody is playing
5. **T3 rig on Unraid** (#15): first run with a green main digest; then #22 and #43 on it; then the #93 trial
6. **Parked:** the iPhone screen redesign and the brand audit (Mark, 31 Oct)
7. **Later this week** — Mark's device checks; TestFlight; #101; the rest of #42 and #96; #59, #56

## Next (P1/P2)

Pushed fixes stay open until a test Claude can run has passed on them (D-58) or, for device checks, until Mark confirms; the lists below are what is not yet built.

- Reliability: #17 export lock (steps now logged; gate after release) · #22 Home mixes latency · #25 harness
  attribution
- Mark's requests: #59 album songs delay · #56 tap-to-play mixes
- Product and admin: #23 progressive relay · #39 cellular checks (Mark)
- Device acceptance (Mark, T5): #95, one checklist of the eleven former acceptance issues

## Later (P3)

#96 smaller server findings (event loop, warming fairness, resilience, Soulseek imports) · #97 smaller web, iPhone
and Watch findings · #86 hardening · #46 native split · #93 apply the 1,475 consistent-tag changes safely · set-aside
ideas (Mac app, Shazam, CarPlay, mix tuning, split DNS) live in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside), not in
issues. Lower-severity security drafts are tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
