# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 20 Nov 2026, evening (Melbourne).*

- **Live:** release `screens-20261119-v1` (server `91bc97d`). **main** now also has batch F (web findings #271–#275,
  #269, server #268, and the native test pins brought up to D-120): two Codex rounds, fixes confirmed, no high left. Not
  yet released.
- **Held back from batch F, each parked on a branch with Codex's findings on its issue:** #266 Undo for Remove from
  playlist (`batch-g-266-parked`; a high: closing the tab can lose queued removals, wants a server bulk remove) and #270
  saved listens across tabs (`batch-270-parked`; one medium left under a full store). #267 needs Mark's design choice.
- **TestFlight:** build 2002 (batch D and E) is on Mark's iPhone and Watch; #265 closed. **Device round done 20 Nov**
  (Reviews A–F): closed #248, #249, #183, #252, #168, #245, #239, #246, #229, #233, #234. Found and filed for the next
  iPhone batch: #284 (menu offers "Load favourite status"), #285 (New playlist inside Add to playlist), #286 (extra icon
  on the Playlists menu button), #287 (no Edit on the queue), #290 (large "Searching" text), #291 (Preferences keeps the
  old theme), #292 ("Ready to play", D-122), #293 (Watch Undo's grey circle), #124 (Shazam still listens for ever: the
  export had no `shazam` lines at all), and #283 (three style rules broken in batch E, two design calls open, and the
  native source tests never run on GitHub: five of them fail on main until #283 lands).
- **YouTube** stays (D-121); #193 closed; #282 asks why it finds no match for 41 of 52 lookups.
- **Tap-through tests** on main `12d27d2`: the first run was cancelled at the time limit; the rerun failed only
  `ServerTests.testSendAnAlbumToTheWatch` ("Not found: Artists…"); rerun again on 20 Nov. Twice a real failure means
  the Mac. They are timing-sensitive; an empty iPhone log is a runner problem.
- **Still waiting on Mark:** the two-day Watch delivery test (#219) and its diagnostics export; #232 and #235 if a failed
  download or request turns up; #184's larger-text step passed, Shazam did not. Open questions for him, one at a time:
  #267 (what Play does on a catalogue card), #242 (queue limit numbers, proposal on the issue), #283's two design calls
  (large titles on the Library pages; Shuffle's button style), #266's two sentences.
- **Decisions:** D-121, D-122 (20 Nov). Next: D-123.
- **Mac:** reachable as `ssh mac` (its address changes; match the host key); debugging only.
- **Worktrees:** cleared on 20 Nov except seven kept for unmerged or private files (see `git worktree list`).

## Now

1. **Next iPhone and Watch batch** from the device round: #284–#287, #290–#293, #283 (with the pytest step in
   `native.yml`), #124 (Shazam), then one TestFlight build.
2. **Release batch F** to the live server once CI passes.
3. **Mark's two-day Watch test** (#219), then its export.
4. **Retirement, 28 Nov** (#201): remove what batches 4, 6 and 8 replaced once #191's pass marks are met.
5. **Live checks that close with use:** #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

#266 (with a server bulk remove) and #270 as their own web batches; #282, #288, #289 (server findings); #242 once Mark
has chosen the numbers; #267 once designed. iPhone findings #259–#261, #276, #277, #280, #253, #281; then #279 (gapless
queue edits in the shared engine) as its own batch.

Then: #226 (an unreadable download list or a source change must not empty the Watch), #230 and #231 (findings), #225
(test workers' memory growth).

## Later

#247 album and artist pages that look the same wherever you open them (parked by Mark, 18 Nov) · #204 concerts on artist pages (Mark picks a provider and area) · #205 Music Haptics (if Apple allows a third-party
player) · #121 Apple Music playlists as your own, downloading the missing songs · #198 Mornye-Python as a lossless
source (only if Monochrome fails) · #200 wrong-song check against YouTube audio (YouTube is kept, D-121) · #173 part 2
shared move journal (design for Mark first) · #96 the two library marker files · #46 remaining (AccountSession,
NativeService per domain) · clean the organisation undo copies once Mark is happy (#93 step 5) · set-aside ideas
(Mac app, CarPlay, mix tuning, split DNS) in [docs/PRODUCT.md](docs/PRODUCT.md#set-aside). Lower-severity security
drafts are tracked privately.

## How to pick work

Take the first open item in **Now** unless Mark says otherwise. New requests from Mark become issues first. Labels:
`bug`, `feature`, `finding`; `P0`–`P3`; `area:*`; `needs-device-test` (Mark's devices, T5); `owner` (waiting on Mark).
