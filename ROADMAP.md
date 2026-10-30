# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 30 Oct 2026 (late evening).*

- **Live:** server `e1557ec` (released 30 Oct 10:22 UTC as `reset-20261030-v3`; rollback container
  `PigMusic-before-reset-20261030-v3`). iPhone/Watch: native **0.2.0 (13)** on Mark's iPhone (free signing,
  reinstall before 6 Nov).
- **Pushed, not yet released** (each batch passed two Codex rounds; withdrawn changes are noted on their issues):
  - Sign-in leftovers (private advisory): warning renewed daily and read apart from the feed, IPv6 counted per /64,
    ~1,400 common passwords refused, bounded hashing queue, People pages off the event loop (#16).
  - Idle CPU and lock leftovers: #88, #74 part 3, the preloader's settings reads (#63), #87. #74 part 2 withdrawn.
  - Web fixes: #60 (web), #80 part 1, #72, #73. #80 part 2 (offline sign-out) withdrawn.
  - Server reliability: #75, #24, #67, #26, #58, #69. #68 withdrawn.
  - iPhone screens, native **build 14**: #60, #61, #62 (not installed: signing needs Mark's Mac password).
- **Live gates:** #20/#16 idle windows met on `1bc2039` and `e1557ec` (0 stalls, 0 lock holds); the loaded window
  waits for real use. Idle CPU on `e1557ec`: 11.5% of a core (was 31.3%), on #63.
- **Ways of working:** area batches, two Codex rounds, Claude pushes (D-50); Claude may release when it helps testing,
  under D-52; the Unraid test copy is approved (D-53, #15).
- **Household details** live only in the gitignored `.private/` (D-45).
- **Waiting on Mark:** device checks for builds 13/14 (#18, #19, #64, #65, #70, #71, #60–#62); #17's device numbers;
  confirming the recommended choices used for #69, #72 and the #62 reading; whether the web Library loses its shuffle
  button too (#61); the decisions in #76, #77, #80 part 2; the [`owner`](https://github.com/maroge1990/PigMusic/issues?q=is%3Aopen+label%3Aowner)
  items; the Cloudflare cache-bypass and IPv6 rate-limit confirmations (sign-in advisories).

## Now

Reliability first (D-47). In order:

1. **Release and live gates** — release the pushed batches under D-52; the loaded 30-minute window for #16/#20; idle
   CPU re-measure (#63)
2. **Device checks** — install build 14 (Mark's Mac password, or TestFlight later); Mark's checks and battery runs
   (#18, #19; #64, #65, #70, #71, #60–#62)
3. **Withdrawn designs** — #68 (hide only the retired file, by hash), #74 part 2 (Tidy must not read a stale library),
   #80 part 2 (deferred sign-out must not touch a new session)
4. **iPhone/Watch playback** — #78 (route change while paused), #76 and #77 (recommended choices, Mark to confirm)
5. **T3 test rig** (#15), approved in D-53

## Next (P1/P2)

Pushed fixes stay open until Mark confirms them; the lists below are what is not yet built.

- Reliability: #17 export lock (needs Mark's device numbers) · #21 cold popularity · #22 Home mixes latency ·
  #25 harness attribution
- Mark's requests: #59 album songs delay · #53 iOS search · #54 song search speed · #55 more popular songs ·
  #56 tap-to-play mixes
- Product and admin: #23 progressive relay · #27 recovery decisions · #39 internet access checks
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
