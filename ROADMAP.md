# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 30 Oct 2026.*

- **Live:** server `3aaaa51` and native **0.2.0 (12)** (owner-reported). Build-12 live checks on 29 Oct failed the
  event-loop (#16) and export-lock (#17) gates; cold popularity is slow (#21); device battery gates are open (#18, #19).
- **Local, not pushed:** server fixes `cc37394`, `73f9ca1`, `d093295`, `7c9f33f` (event-loop blocks found live and by
  audit; reserved sign-in threads; each with a regression that fails on the old code and two Codex rounds), the
  documentation reset `6d4ebd3`, and the household-details clean-up `4f666f3`, `c8eead2` (two Codex rounds, approved),
  plus notes `14a36dc`, `ca6b1c8`. Not yet live (#20).
- **Household details** (addresses, host names, domain, edge rules) live only in the gitignored `.private/` (D-45).
- **Project reset done** (30 Oct): review and fixes, documentation structure, issues #15–#86 (Mark's own requests
  #53–#63), whole-product audit (findings #64–#86 plus five private security drafts), and Mark's top five below.
  The feature freeze ends; new requests become issues first.
- **Waiting on Mark:** the export-lock device check (#17), device evidence for battery (#18, #19), the items labelled
  [`owner`](https://github.com/maroge1990/PigMusic/issues?q=is%3Aopen+label%3Aowner).

## Now

Chosen by Mark on 30 Oct (D-47), reliability first, in this order:

1. **Release the 30 Oct server fixes** — #20, confirming #16 live (and #17's evidence once Mark's device check is in)
2. **Watch delivery and battery** — #64, #70, #71, #65 (most likely closes #19; then #18 evidence)
3. **Web listening history stops** — #66
4. **Sign-in hardening** — the two internet-facing draft security advisories (sign-in throttling and the cookie on
   cacheable artwork); tracked privately, not as public issues
5. **Constant server CPU while idle** — #63, with #74 and #79

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
