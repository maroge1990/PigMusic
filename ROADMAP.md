# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 31 Oct 2026 (evening, Melbourne).*

- **Live:** server `99b49eb` (released 30 Oct 23:42 UTC as `reset-20261031-v3`; rollback container
  `PigMusic-before-reset-20261031-v3`). Container recreated 31 Oct without the plain LAN port (#39), same image;
  rollback `PigMusic-before-port-20261031`. iPhone/Watch: native **0.2.0 (15)** on Mark's iPhone since 31 Oct (free
  signing, expires about 7 Nov).
- **Pushed 31 Oct evening, not yet released:** the server batch, two Codex rounds (round 1 medium fixed, round 2
  approve): YouTube removed as a source (#94, D-60); a long `lifecycle_lock` hold now names its steps, export
  publication first (#17); with export off a replaced file goes to Trash instead of being deleted (#96). Earlier on
  31 Oct: the CI diet (#91) and the simulator screenshot job (#92).
- **iPhone screen redesign parked** by Mark (31 Oct): he thinks the four mockups need redoing. The brand audit waits
  with it (same screens). Mark's new player reports (31 Oct) are issues of their own.
- **Ways of working:** area batches, one Codex round (two for risky or large batches, D-59), Claude pushes (D-50);
  Claude may release when it helps testing (D-52); efficiency and battery first (D-54); Claude closes an issue once a
  test it can run has passed on the fix, device checks still wait for Mark (D-58). Local suite: `pytest -n 4`, under
  a minute.
- **Live gates owed:** the first night's search count on `99b49eb` (#90, compare ~700 / ~7 h), the quiet idle-CPU
  re-measure (#63, compare 11.5% on `e1557ec`), the loaded 30-minute window (#16/#20), the first nightly scale-gate
  run (#91); after the next release, remote-playback "unavailable" time without YouTube (#94) and any lock-hold
  warning's steps (#17).
- **Household details** live only in the gitignored `.private/` (D-45).
- **Waiting on Mark:** the cellular checks on #39; when to run the tag job (#93); his device checks (sections B–F of
  his list: #18, #19, #60–#62, #64, #65, #70, #71, #76–#78); the
  [`owner`](https://github.com/maroge1990/PigMusic/issues?q=is%3Aopen+label%3Aowner) items; the Cloudflare
  cache-bypass and IPv6 rate-limit confirmations (sign-in advisories); TestFlight once Apple approves his membership.

## Now

Reliability first (D-47), with Mark's player reports. In order:

1. **iPhone player fixes** — Mark's 31 Oct reports (loading spinner, tap Up next, the ⋯ menu with favourite star);
   with #59, #53, #54, #56 as native build 16
2. **Live gates** — #90, #63, #16/#20 measurements; the first nightly scale-gate run (#91)
3. **Release the server batch** (#94, #17, #96) under D-52 once CI is green and nobody is playing
4. **T3 rig on Unraid** (#15): first run with a green main digest; then #22 and #43 on it; then the #93 trial
5. **Parked:** the iPhone screen redesign and the brand audit (Mark, 31 Oct)
6. **Later this week** — Mark's device checks; TestFlight; the rest of #42 and #96

## Next (P1/P2)

Pushed fixes stay open until a test Claude can run has passed on them (D-58) or, for device checks, until Mark confirms; the lists below are what is not yet built.

- Reliability: #17 export lock (steps now logged; gate after release) · #22 Home mixes latency · #25 harness
  attribution
- Mark's requests: #59 album songs delay · #53 iOS search · #54 song search speed · #55 more popular songs ·
  #56 tap-to-play mixes
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
