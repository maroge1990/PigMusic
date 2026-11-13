# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 13 Nov 2026 (late afternoon, Melbourne).*

- **Live:** release `web-20261114-v1`, server `a15d53c`, since 13 Nov about 16:05 Melbourne (D-87: releases at any
  time; Mark ran the apply step). It carries batches 'brand' (#179), 'idle' (#171, #172) and 'web' (#174, #175, #181,
  #182) on top of the artwork release. Healthy. Rollback: container `PigMusic-before-web-20261114-v1`, backup
  `pre-web-20261114-v1` (image `sha-8ee4c29`). D-78 clean-up of the older rollbacks (artwork 13 Nov, findings 9 Nov)
  waits on Mark's hands.
- **Live checks after this release:** the web changes are in the served bundle; #174, #175, #179, #181 and #182 are
  closed (D-58). The stall watcher thread now uses 0.02% of a core (#172). But idle CPU rose: the server used 3.10% of
  a core over 10 quiet minutes against the 0.97% baseline, spent in the asyncio worker threads with SQLite-like file
  work and nothing logged: **#190**, not yet explained (this batch, batch 4 or the morning's artwork release). Lock
  holds: only the start-up one in the first half hour; #171 waits for tonight's export and mix run to compare.
- **Audit of 13 Nov:** report https://claude.ai/artifact/91KX6g4ciEB7npgbpR4aCH, handover
  https://claude.ai/artifact/C5NUCuD6V6v8EaYR2n5b7G, issues #168–#190. Batches 1 to 5 done. Next: #190, then batch 6
  agency and words (#183), 7 server dead code (#169, #170, #173), 8 YouTube and SoundCloud removal (#94), 9 iPhone dead
  code and search merge (#176–#178), 10 the review checklist (#184).
- **Pushed, not on a device yet:** batch 'iphone-brand' (#180), `0addcab`..`50c9099`: the splash ends when the saved
  library is shown; the pig with one line on empty and error screens (D-82) and a Library error state; pages say what
  is opening; plain words for sync and settings; "Couldn't download"; rounded figures where compared (D-84); a symbol
  per Watch state on song rows. Two Codex rounds, both approve with no findings. Icons are left for batch 10.
- **D-86:** Mark tests once, when all audit batches are done. The native batches (5, the native half of 6, 9, and the
  #188 app fix) go to TestFlight together as 0.2.0 (25) at the end; no build bump until then.
- **#188 (P1):** iPhone requests fail for 10–20 s after opening the app although the server answered. Cloudflare
  HTTP/3 switched off 13 Nov about 12:10 Melbourne as a test; compare Mark's next diagnostics export with the 13 Nov
  baseline (54 failed of 166 logged requests).
- **CI:** flakes in #189. The container check on `55ddf4c` failed on a racy web test (Your devices Loading), fixed in
  `e1b7623` (the test now answers the quality request).
- **iPhone/Watch:** latest TestFlight 0.2.0 (24); its items (#168) and build 23's (#149, #157–#163) wait for Mark's
  testing round (D-86).
- **Ways of working:** area batches (D-50), Codex rounds per D-59 and D-71, Claude pushes; releases at any time under
  D-87; D-58 closing. Household details live only in the gitignored `.private/` (D-45).

## Now

1. **#190** idle CPU three times the baseline after the 13 Nov releases: find the periodic worker-thread job.
2. **Audit batches 6 to 9** in the handover's order (#183, #169/#170/#173, #94, #176–#178); server batches released
   as they are ready (D-87).
3. **#171** count tonight's lifecycle_lock holds against the baseline; close if they fell.
4. **#188** compare Mark's next diagnostics export; the app fix rides in the final TestFlight build.
5. **End of the audit:** TestFlight 0.2.0 (25), batch 10 (#184) and Mark's single testing round (D-86): builds 23 to 25,
   #168, #180, the web, icons and accessibility settings.
6. **Live checks from real use:** #16/#17/#20 freezes and lock holds, #22, #54, #94, #23 first-sound p90; #63.
7. **#123** deferred Watch publish never rescheduled after an account change (native).

## Later (P3)

#96 the two library marker files (needs the live export config) · #46 remaining (full AccountSession, NativeService
per domain) · clean the organisation undo copies once Mark is happy (#93 step 5) · set-aside ideas (Mac app, CarPlay,
mix tuning, split DNS) live in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside). Lower-severity security drafts are
tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
