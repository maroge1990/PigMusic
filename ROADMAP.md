# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 30 Oct 2026 (evening).*

- **Live:** server `1bc2039` (released 30 Oct as `reset-20261030-v2`, previous kept as `PigMusic-before-reset-20261030-v2`):
  the 30 Oct event-loop fixes, web listening history (#66), sign-in hardening, release-tooling fixes. The live
  container's template had drifted (br0 network and memory/CPU limits lost on 25 Oct); both restored before the
  release. iPhone: **0.2.0 (12)** installed from the Mac on 30 Oct.
- **Pushed, not yet released:** the idle-CPU batch (`45d0f3b`, `c4a7887`, `a4bf992`, `bc20fc2`) and the Watch
  delivery batch (native **build 13**: `b1a5649`, `8d9e5b6`, `f356ea3`, `7946403`, `4e0cc3c`). Both passed two Codex
  rounds; build 13 is not on Mark's devices yet (signing needs his Mac password in a terminal, or TestFlight).
- **Live gates:** #20/#16 idle 30-minute window met (0 stalls, 0 lock holds); the *loaded* window still waits for real
  use. Idle CPU before the idle-CPU batch: 31% of one core (27.5% export pass, 3.1% health check), posted on #63;
  re-measure after its release.
- **Ways of working:** area batches with one Codex pair per batch, Claude pushes after two rounds, releases need
  Mark's go-ahead (D-50). New decisions today: D-48 (household-song listens), D-49 (per-account sign-in delay),
  D-50, D-51 (Watch copies prepared only in the foreground).
- **Household details** live only in the gitignored `.private/` (D-45).
- **Waiting on Mark:** go-ahead to release the pushed batches; installing build 13 (or choosing TestFlight, which
  needs a paid Apple Developer account); device evidence for #17, #18, #19; the items labelled
  [`owner`](https://github.com/maroge1990/PigMusic/issues?q=is%3Aopen+label%3Aowner); confirming the Cloudflare
  cache bypass and IPv6 rate limit (sign-in advisories).

## Now

Chosen by Mark on 30 Oct (D-47), reliability first. Done today: items 3 and 4 are live; 2 and 5 are pushed.

1. **Release and live gates** — release the pushed batches (#20); take a loaded 30-minute window for #16; re-measure
   idle CPU (#63)
2. **Watch delivery and battery, device checks** — build 13 on phone and Watch, then Mark's battery runs (#19, #18;
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
