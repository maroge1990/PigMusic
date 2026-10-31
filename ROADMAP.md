# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 31 Oct 2026 (evening, Melbourne).*

- **Live:** server `99b49eb` (released 30 Oct 23:42 UTC as `reset-20261031-v3`; rollback container
  `PigMusic-before-reset-20261031-v3`), carrying every server batch of 30 and 31 Oct (#90/#89, #68, #74 part 2,
  #80 part 2, the efficiency batch #21/#44/#42 part, T3 rig phase 1, App Store readiness). iPhone/Watch: native
  **0.2.0 (15)** on Mark's iPhone since 31 Oct (free signing, expires about 7 Nov).
- **Pushed 31 Oct evening, nothing to release:** the CI diet (#91) and the simulator screenshot job (#92), two
  Codex rounds each. Container CI is 5–5.5 min per push (was 10.8): backend suite on four workers, 23 wall-clock
  timing tests reported in the run summary instead of failing the push, OpenAPI contract check inside the image, the
  three 1 GB scale gates on a nightly schedule (16:00 UTC, first run tonight), on manual runs and on release tags.
  Mac CI runs only when `swift/`, `api/` or the brand kit changes. `screens.yml` photographs the six main iPhone
  screens light and dark from the demo library (iPhone 17, iOS 26.5); the first full set is on #92.
- **Mark's review plan, order confirmed 31 Oct:** (1) CI diet + screenshots, done; (2) issue clean-up, one question
  at a time; (3) grounded iPhone screen review with mockups; (4) brand audit. The PigBrand repository is found
  (private GitHub repo, cloned beside this checkout); PigMusic's vendored kit equals PigBrand 1.7.0 exactly.
- **Ways of working:** area batches, two Codex rounds, Claude pushes (D-50); Claude may release when it helps
  testing (D-52); efficiency and battery first (D-54); Claude closes an issue once a test it can run has passed on
  the fix, device checks still wait for Mark (D-58). Local suite: `pytest -n 4`, about a minute.
- **Live gates owed:** the first night's search count on `99b49eb` (#90, compare ~700 / ~7 h), engine exits (#89),
  the quiet idle-CPU re-measure (#63, compare 11.5% on `e1557ec`), the loaded 30-minute window (#16/#20).
- **Household details** live only in the gitignored `.private/` (D-45).
- **Waiting on Mark:** whether ordinary batches may have one Codex round instead of two (D-59 candidate); the
  issue clean-up questions; his device checks (sections B–F of his list: #18, #19, #60–#62, #64, #65, #70, #71,
  #76–#78); #17's device numbers; the [`owner`](https://github.com/maroge1990/PigMusic/issues?q=is%3Aopen+label%3Aowner)
  items; the Cloudflare cache-bypass and IPv6 rate-limit confirmations (sign-in advisories); TestFlight once Apple
  approves his membership.

## Now

Mark's review plan first, then reliability (D-47). In order:

1. **Issue clean-up** — the 7 owner decisions one at a time; fold the acceptance placeholders (#28–#38) into one
   checklist; close the set-aside ideas (#48–#52); merge the small P3 findings; close pushed fixes as their tests
   pass (D-58)
2. **iPhone screen review** — from the #92 pictures: mockups for Library segments, Downloads as one page, one album
   template, a compact song action list; then one fix batch as build 16 with #59, #53, #54, #56
3. **Brand audit** — web, iPhone and Watch against PigBrand 1.7.0 (taglines, nav labels, radii, Watch colour
   roles); one fix batch
4. **Live gates** — #90, #89, #63, #16/#20 measurements; the first nightly scale-gate run (#91)
5. **T3 rig on Unraid** (#15): first run with a green main digest; then #22 and #43 on it
6. **Later this week** — Mark's device checks; TestFlight; the rest of #42

## Next (P1/P2)

Pushed fixes stay open until a test Claude can run has passed on them (D-58) or, for device checks, until Mark confirms; the lists below are what is not yet built.

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
