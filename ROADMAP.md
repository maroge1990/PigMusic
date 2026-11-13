# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 13 Nov 2026 (afternoon, Melbourne).*

- **Live:** release `artwork-20261113-v1`, server `8ee4c29`, since 13 Nov 09:37 Melbourne (D-52: nobody playing; Mark
  ran the apply step himself because the permission system blocks Claude from changing the live container). Healthy;
  start-up lock holds 1.1 s as before. Rollback: container `PigMusic-before-artwork-20261113-v1`, backup
  `pre-artwork-20261113-v1`. D-78 clean-up of the 9 Nov rollback still to do (Mark's hands).
- **Audit of 13 Nov** (efficiency, duplication, brand 1.8/1.9, UX against the Apple design guide, artwork): report at
  https://claude.ai/artifact/91KX6g4ciEB7npgbpR4aCH; issues #168–#185. Batch order is in the report: artwork (#168,
  done) → brand re-vendor (#179) → lock contention and idle work (#171, #172) → web (#174, #175, #181, #182) → iPhone
  brand rules (#180) → agency and words (#183, waits on Mark) → server dead code (#169, #170, #173) → iPhone dead
  code and search merge (#176–#178) → review checklist (#184).
- **In this release:** batch 'artwork' (#168, #185): `312d21f` mix covers warmed when published and listed, `4031df8`
  iPhone asks again for a cover the server is still fetching (lock screen too), `f4cd866` only shown songs warmed and
  failed Fanart attempts rest. Two Codex rounds + one confirmation, no findings left; backend 976, PigKit 332, iPhone
  and Watch simulator builds OK. **Live check next:** after tonight's mix run, the warmer log should show covers
  prepared at publish time and Home mixes should open with covers on the web; the iPhone retry needs TestFlight build
  0.2.0 (24), not yet built.
- **Pushed, not released:** batch 'brand' (#179): `7f70104` Pig family 1.9.0 vendored with its glass stylesheet (no
  asset byte changed), `d5b770f` brand glass on the song menu and phone dock, solid under Reduce Transparency (and the
  build no longer strips the standard `backdrop-filter`, so Chromium shows the blur again), `40828c0` rounded tabular
  figures on admin glance and queue progress (D-84), `a073184` light default theme colour. One Codex round: no high
  finding; two medium ones filed as #186 and #187. Rides with the next server release (after batch 3).
- **Pushed, not released:** batch 'idle' (#171, #172): `7bd2b28` mix cache cleanup once a pass at most, every 10
  minutes or at once when the cache is full (was twice a minute under lifecycle_lock); `b39a45b` + `442d2ad` the stall
  watch sleeps until a stall could have begun (was 20 wake-ups a second); `30834e2` relay folder pruned every 10
  minutes or after 500 MB (was before every download). The artist-portrait part of #172 was reverted (`55e5939`):
  repeat passes already read cached answers. Two Codex rounds: round 1 found two medium issues in the batch, both
  fixed and confirmed in round 2 (approve). **Live check after release:** lifecycle_lock hold warnings per hour and idle
  CPU against the 9 Nov baselines (server 0.97%, engine 2.41%).
- **Decisions 13 Nov:** D-79 Undo over confirm (#183), D-80 drop SoundCloud and yt-dlp with #94 (Monochrome enabled live but
  never searched: check its configuration in #94), D-81 navigation names stay, D-82 the pig on iPhone empty and error
  states (#180), D-83 no Watch artwork (set aside), D-84 rounded tabular figures only where compared (#179, #180), D-85 brand glass where it fits on the web (#179). D-78
  clean-up of the 9 Nov rollback still waits on Mark's hands.
- **iPhone/Watch:** 0.2.0 (24) uploaded to TestFlight 13 Nov 10:00 Melbourne (`32c9b28`; the artwork retry, #168), waiting
  on Mark's device check: open a mix, check covers and the lock screen artwork. Build 23's items (#149, #157–#163)
  still wait on Mark's device check too; Mark also owes a look at the web (#150–#156).
- **Ways of working:** area batches (D-50), Codex rounds per D-59 and D-71, Claude pushes; releases under D-52/D-73;
  D-58 closing. Household details live only in the gitignored `.private/` (D-45). The Mac was cleaned on 13 Nov
  (PigTV and PigRider build folders, iPad symbol cache): 15 GB free after the build.

## Now

1. **Mark on build 24 (T5):** covers in a mix and on the lock screen (#168); then the audit batches in the report's
   order: brand (#179) and idle work (#171, #172) are pushed and go out in the next server release; next is the web
   batch (#174, #175, #181, #182). Live check of the artwork release after tonight's mix run.
2. **#188** iPhone requests fail for 10–20 s after opening the app although the server answered them (Mark's 13 Nov
   export): log the connection error and protocol, widen the retry window; decides whether batch 9's TestFlight build
   or its own carries it.
3. **Mark on build 21 (T5)**, then send diagnostics: Shazam (identify, Shazamed, Share from the Shazam app), Add to my
   playlists, a few songs not on the server (#23 timing, #94), Home a few times (#22), searches (#54).
4. **#123** deferred Watch publish never rescheduled after an account change (native; build 22 with any device findings).
5. **Live checks from real use:** #16/#17/#20 freezes and lock holds (nightly mix run + Mark's listening), #22, #54,
   #94, #23 first-sound p90; #63 quiet-night idle measurement.
6. **Rig:** a larger generated library for #22 timings; #96 warming fairness; check the engine sweep against the real
   slskd layout.

## Later (P3)

#96 the two library marker files (needs the live export config) · #46 remaining (full AccountSession, NativeService
per domain) · clean the organisation undo copies once Mark is happy (#93 step 5) · set-aside ideas (Mac app, CarPlay,
mix tuning, split DNS) live in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside). Lower-severity security drafts are
tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
