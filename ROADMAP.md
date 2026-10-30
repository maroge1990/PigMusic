# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 30 Oct 2026.*

- **Live:** server `3aaaa51` and native **0.2.0 (12)** (owner-reported). Build-12 live checks on 29 Oct failed the
  event-loop (#16) and export-lock (#17) gates; cold popularity is slow (#21); device battery gates are open (#18, #19).
- **Local, not pushed:** `14a36dc`, `cc37394`, `73f9ca1`, `d093295`, `7c9f33f`, `ca6b1c8` and the documentation
  reset. The server commits fix the event-loop blocks found live and by audit, and give sign-in checks reserved
  threads; each fix has a regression that fails on the old code, and each batch had two Codex rounds. Not yet live (#20).
- **Project reset in progress** (playbook steps 0–7): steps 1–3 done; documents migrated into this structure and the
  old ones archived; whole-product audit (step 5) and Mark's roadmap choices (step 6) are next.
- **Waiting on Mark:** the export-lock device check (#17), device evidence for battery (#18, #19), the items labelled
  [`owner`](https://github.com/maroge1990/PigMusic/issues?q=is%3Aopen+label%3Aowner).

## Now

Provisional until Mark picks the top five in step 6:

1. #20 Release the 30 Oct server batch and re-run the live gates
2. #16 Confirm the event loop no longer blocks on the live server
3. #17 Export publication lock (needs Mark's device check)
4. #18, #19 iPhone and Watch battery (needs device evidence)
5. #15 Build the T3 test rig

## Next (P2)

- #21 cold popularity · #22 Home mixes latency · #23 progressive remote relay
- #24 retired-edition stand-ins · #25 harness failure attribution · #26 session renewal on reserved threads
- #27 export and Tidy recovery decisions · #39 internet access checks
- Acceptance on devices: #28 sync · #29 Watch delivery · #30 week without crashes · #31 artwork · #32 playback ·
  #33 mixes · #34 sources · #35 screens · #36 release checklist · #37 editions

## Later (P3)

#38 small checks · #40 namesake choice · #41 YouTube access · #42 smaller loop blocks · #43, #44 warming and
popularity efficiency · #45 diagnostics crowding · #46 native split · #47 merged branches · set aside: #48 Mac app,
#49 Shazam, #50 CarPlay, #51 mix tuning, #52 split DNS

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
