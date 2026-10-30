# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 30 Oct 2026 (evening).*

- **Live:** server `e1557ec` (released 30 Oct 10:22 UTC as `reset-20261030-v3`; rollback container
  `PigMusic-before-reset-20261030-v3`): the 30 Oct event-loop fixes, web listening history (#66), sign-in hardening,
  the Watch delivery batch's server side, the idle-CPU batch (export pass, health check, artwork rebuilds) and the
  release-tooling fixes. The live template had drifted (br0 and limits lost on 25 Oct); restored before release.
  iPhone/Watch: native **0.2.0 (13)** installed on Mark's iPhone on 30 Oct (Watch delivery batch: #64, #70, #71, #65).
- **Pushed, not released:** nothing.
- **Live gates:** #20/#16 idle 30-minute window met on `1bc2039` (0 stalls, 0 lock holds); a loaded window and the
  idle-CPU re-measure on `e1557ec` are being taken (baseline before: 31% of one core, on #63).
- **Ways of working:** area batches with one Codex pair per batch, Claude pushes after two rounds, releases need
  Mark's go-ahead (D-50). New decisions today: D-48 (household-song listens), D-49 (per-account sign-in delay),
  D-50, D-51 (Watch copies prepared only in the foreground).
- **Household details** live only in the gitignored `.private/` (D-45).
- **Waiting on Mark:** build-13 device checks (#64, #65, #70, #71, battery #18, #19) and #17 evidence; reinstalling
  build 13 before 6 Nov (free-account signing) or choosing TestFlight (paid Apple Developer account); the items labelled
  [`owner`](https://github.com/maroge1990/PigMusic/issues?q=is%3Aopen+label%3Aowner); confirming the Cloudflare
  cache bypass and IPv6 rate limit (sign-in advisories).

## Now

Chosen by Mark on 30 Oct (D-47), reliability first. Done today: all five are live; what remains is below.

1. **Live gates** — a loaded 30-minute window for #16/#20; the idle-CPU re-measure on #63
2. **Watch delivery and battery, device checks** — build 13 is installed; Mark's checks and battery runs (#19, #18;
   #64, #70, #71, #65 are fixed in it)
3. **Sign-in hardening leftovers** — the warning not renewed during multi-day guessing (Codex round 2), IPv6 address
   grouping, a longer rejected-password list, bounding the hashing queue; tracked in the private advisory
4. **Idle CPU leftovers** — #74 parts 2–3 (publication refresh under the lifecycle lock, stuck replacement cleanup),
   the preloader's per-item settings stat, #88
5. **Findings from today's reviews** — #87 (release check tmpfs size), #88

## Next (P1/P2)

- Reliability: #17 export lock · #67 mix playback lock · #68 replacement duplicate · #69 remaster validation ·
  #75 release script ordering · #76 queue cleared by a changed download · #77 Watch budget · #78 paused route change ·
  #73 live updates reconnect · #21 cold popularity · #22 Home mixes latency · #24 stand-ins · #25 harness attribution ·
  #26 session renewal
- Mark's requests: #60 no "Preparing audio…" text · #61 Library shuffle icon · #62 album tile layout ·
  #58 radio opening song · #59 album songs delay · #53 iOS search · #54 song search speed · #55 more popular songs ·
  #56 tap-to-play mixes
- Product and admin: #72 People confirmations · #80 web player and sign-out · #23 progressive relay · #27 recovery
  decisions · #39 internet access checks
- Test rig: #15 (moves to Now when the first five are done)
- Device acceptance (Mark, T5): #28 sync · #29 Watch delivery · #30 week without crashes · #31 artwork · #32 playback ·
  #33 mixes · #34 sources · #35 screens · #36 release checklist · #37 editions

## Later (P3)

#57 mix completion behaviour · #81 provider resilience · #82 Soulseek imports · #83, #84, #85 smaller audit findings ·
#86 hardening · #38 small checks · #40 namesake choice · #41 YouTube access · #42 smaller loop blocks · #43, #44
warming efficiency · #45 diagnostics crowding · #46 native split · #47 merged branches · set aside: #48 Mac app,
#49 Shazam, #50 CarPlay, #51 mix tuning, #52 split DNS. Lower-severity security drafts are tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
