# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 31 Oct 2026 (early morning, Melbourne).*

- **Live:** server `15566c4` (released 30 Oct 13:53 UTC as `reset-20261031-v2` under D-52; rollback container
  `PigMusic-before-reset-20261031-v2`). It carries every batch pushed on 30 Oct: sign-in leftovers (private
  advisory), idle CPU and lock leftovers (#88, #74 part 3, #63 preloader, #87), web fixes (#60, #80 part 1, #72, #73)
  and server reliability (#75, #24, #67, #26, #58, #69). Two test-only fixes went first: the native screen checks skip
  in the image (f23b2cf) and a diagnostics test tolerates a real stall (15566c4).
- **iPhone/Watch:** native **0.2.0 (13)** on Mark's iPhone (free signing, reinstall before 6 Nov). Build **14** (#60,
  #61, #62) is pushed, not installed: signing needs Mark's Mac password.
- **Withdrawn after high Codex findings** (designs on the issues): #68, #74 part 2, #80 part 2.
- **Live gates:** #20/#16 idle windows met on `1bc2039` and `e1557ec`; the loaded window waits for real use. Idle CPU
  on `e1557ec`: 11.5% of a core (was 31.3%); on `15566c4` the first window fell in the midnight mix searches (30%,
  not idle), so the quiet re-measure is still owed on #63.
- **New finding:** the download engine aborted once on a burst of search replies (256 MB .NET heap cap), restarted
  itself (#89).
- **Flaky timing tests:** `test_mix_responsiveness` (0.503 s vs 0.5 s) and the diagnostics stall test both tripped on
  a busy host; leftover watchdogs from earlier tests are the likely shared cause.
- **Ways of working:** area batches, two Codex rounds, Claude pushes (D-50); Claude may release when it helps testing,
  under D-52; the Unraid test copy is approved (D-53, #15).
- **Household details** live only in the gitignored `.private/` (D-45).
- **Waiting on Mark:** device checks for builds 13/14 (#18, #19, #64, #65, #70, #71, #60–#62) and the server/web
  checks for the 31 Oct release; #17's device numbers; confirming the recommended choices used for #69, #72 and the
  #62 reading; whether the web Library loses its shuffle button too (#61); the decisions in #76, #77, #80 part 2; the
  [`owner`](https://github.com/maroge1990/PigMusic/issues?q=is%3Aopen+label%3Aowner) items; the Cloudflare
  cache-bypass and IPv6 rate-limit confirmations (sign-in advisories).

## Now

Reliability first (D-47). In order:

1. **Live gates** — the loaded 30-minute window for #16/#20; quiet idle-CPU re-measure on `15566c4` (#63)
2. **Overnight mix searching** (#90, with the engine heap crash #89) — efficiency and battery are the focus (Mark,
   31 Oct): stop re-searching songs that can't be found
3. **Device checks** — install build 14 (Mark's Mac password, or TestFlight later); Mark's checks and battery runs
   (#18, #19; #64, #65, #70, #71, #60–#62)
4. **Withdrawn designs** — #68 (hide only the retired file, by hash), #74 part 2 (Tidy must not read a stale library),
   #80 part 2 (deferred sign-out must not touch a new session)
5. **iPhone/Watch playback** — #78 (route change while paused), #76 and #77 (recommended choices, Mark to confirm)
6. **T3 test rig** (#15), approved in D-53

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
