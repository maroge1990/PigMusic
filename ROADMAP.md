# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 31 Oct 2026 (morning, Melbourne).*

- **Live:** server `15566c4` (released 30 Oct 13:53 UTC as `reset-20261031-v2` under D-52; rollback container
  `PigMusic-before-reset-20261031-v2`), carrying every batch pushed on 30 Oct.
- **Pushed 31 Oct, release scheduled** under D-52 for 00:05 Melbourne on 1 Nov (`reset-20261101-v1`, newest main
  commit whose container CI passes, only while nobody is playing):
  - Overnight mix searching (#90) and the engine heap (#89): unfindable songs are searched once per household per
    20 h; engine heap 512 MB.
  - Withdrawn designs rebuilt: #68 (only the retired file itself is hidden, by hash, while its replacement plays),
    #74 part 2 (publication rebuilds outside the lock; locked operations catch the library up first), #80 part 2
    (offline web sign-out ends that one session by id later; /me returns `session_id`). #80's round-2 high finding
    was fixed without a third review (two-round limit).
- **iPhone/Watch:** native **0.2.0 (13)** on Mark's iPhone (free signing, reinstall before 6 Nov). Build **14** (#60,
  #61, #62) is pushed, not installed: signing needs Mark's Mac password.
- **Live gates:** #20/#16 idle windows met; the loaded window waits for real use. Idle CPU on `e1557ec`: 11.5% of a
  core; on `15566c4` the server never went quiet overnight (mix searching ran past 07:30), so the re-measure (#63)
  waits for #90 to be live. After the 1 Nov release, compare that night's searches with ~700 / ~7 h (#90).
- **Flaky timing tests:** `test_mix_responsiveness` (0.503 s vs 0.5 s) once in CI; the diagnostics stall test and the
  radio-opening test were fixed.
- **Ways of working:** area batches, two Codex rounds, Claude pushes (D-50); Claude may release when it helps testing,
  under D-52; efficiency and battery first (D-54); the Unraid test copy is approved (D-53, #15).
- **Household details** live only in the gitignored `.private/` (D-45).
- **Waiting on Mark:** the server/web checks for the 31 Oct and 1 Nov releases and device checks for builds 13/14;
  confirming #80 part 2's behaviour (clear this device and say so); #17's device numbers; the recommended choices
  used for #69, #72 and the #62 reading; whether the web Library loses its shuffle button too (#61); #76, #77; the
  [`owner`](https://github.com/maroge1990/PigMusic/issues?q=is%3Aopen+label%3Aowner) items; the Cloudflare
  cache-bypass and IPv6 rate-limit confirmations (sign-in advisories).

## Now

Reliability first (D-47). In order:

1. **Release and live gates** — the 1 Nov release; then that night's search count (#90), the engine log (#89), the
   quiet idle-CPU re-measure (#63) and the loaded 30-minute window for #16/#20
2. **Device checks** — install build 14 (Mark's Mac password, or TestFlight later); Mark's checks and battery runs
   (#18, #19; #64, #65, #70, #71, #60–#62)
3. **iPhone/Watch playback** — #78 (route change while paused), #76 and #77 (recommended choices, Mark to confirm),
   native build 15
4. **T3 test rig** (#15), approved in D-53
5. **Efficiency leftovers** (D-54) — #42, #43, #44 if the re-measure shows they matter

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
