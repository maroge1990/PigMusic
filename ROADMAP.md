# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 13 Nov 2026 (morning, Melbourne).*

- **Live:** release `findings-20261109-v1`, server `4c0550d`, since 9 Nov 09:06 Melbourne. Healthy. Only this release's
  rollback is kept on Unraid (D-78).
- **Audit of 13 Nov** (efficiency, duplication, brand 1.8/1.9, UX against the Apple design guide, artwork): report at
  https://claude.ai/artifact/91KX6g4ciEB7npgbpR4aCH; issues #168–#185. Batch order is in the report: artwork (#168,
  done) → brand re-vendor (#179) → lock contention and idle work (#171, #172) → web (#174, #175, #181, #182) → iPhone
  brand rules (#180) → agency and words (#183, waits on Mark) → server dead code (#169, #170, #173) → iPhone dead
  code and search merge (#176–#178) → review checklist (#184).
- **Pushed 13 Nov, not yet released:** batch 'artwork' (#168, #185): `312d21f` mix covers warmed when published and
  listed, `4031df8` iPhone asks again for a cover the server is still fetching (lock screen too), `f4cd866` only shown
  songs warmed and failed Fanart attempts rest. Two Codex rounds + one confirmation, no findings left. Backend 976
  passed, PigKit 332 passed, iPhone and Watch simulator builds OK. Needs a release and a TestFlight build (0.2.0 (24))
  before Mark can see the artwork fix; check the mix covers on Home after the next mix run.
- **Mark's decisions pending** (report, last section; ask one at a time): SoundCloud with #94, Undo over confirm
  (#183), navigation names, pig in iPhone empty states, Watch artwork, figures scope.
- **iPhone/Watch:** 0.2.0 (23) on TestFlight since 8 Nov (#149, #157–#163, waiting on Mark's device check). Mark also
  owes a look at the web (#150–#156).
- **Ways of working:** area batches (D-50), Codex rounds per D-59 and D-71, Claude pushes; releases under D-52/D-73;
  D-58 closing. Household details live only in the gitignored `.private/` (D-45). The Mac was cleaned on 13 Nov
  (PigTV and PigRider build folders, iPad symbol cache): 15 GB free after the build.

## Now

1. **Release the artwork batch** (#168, #185) and build 0.2.0 (24) for TestFlight; then the audit batches in the
   report's order, starting with the brand re-vendor (#179).
2. **Mark on build 21 (T5)**, then send diagnostics: Shazam (identify, Shazamed, Share from the Shazam app), Add to my
   playlists, a few songs not on the server (#23 timing, #94), Home a few times (#22), searches (#54).
3. **#123** deferred Watch publish never rescheduled after an account change (native; build 22 with any device findings).
4. **Live checks from real use:** #16/#17/#20 freezes and lock holds (nightly mix run + Mark's listening), #22, #54,
   #94, #23 first-sound p90; #63 quiet-night idle measurement.
5. **Rig:** a larger generated library for #22 timings; #96 warming fairness; check the engine sweep against the real
   slskd layout.

## Later (P3)

#96 the two library marker files (needs the live export config) · #46 remaining (full AccountSession, NativeService
per domain) · clean the organisation undo copies once Mark is happy (#93 step 5) · set-aside ideas (Mac app, CarPlay,
mix tuning, split DNS) live in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside). Lower-severity security drafts are
tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
