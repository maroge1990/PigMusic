# Roadmap

Open work lives in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues). This page orders it; the issues hold
the detail. What PigMusic is: [docs/PRODUCT.md](docs/PRODUCT.md).

## Current state

*Overwritten at the end of every session. 21 Nov 2026, evening (Melbourne).*

- **Live:** release `limits-20261121-v1` (server `3ecb88e`). On top of `iphonefix-20261121-v1` it adds: the old-code
  removal (#201: the instant-listening relay and ArtistGrid portraits; left: #307); the instant-listening match rule of
  D-130 (#282; left: #310); the collection menu on catalogue album and artist cards (D-129, #267 closed; left: #311,
  #309); and the limits on how much a person can queue (D-128, D-131, #242; left: #313, and #312 for downloads the
  server queues by itself, which stay exempt). Every batch: two Codex rounds and confirmations, no high left.
- **main `3ecb88e`** also holds the four native batches G, L, K, J (see the decisions D-121 to D-131) and the iPhone
  half of the queue limits. Both apps build and the PigKit and tap-through tests pass on the Mac on iOS 27.0 and
  watchOS 27.2.
- **TestFlight:** builds 2003, 2004, 2005 arrived (G; G+L; G+L+K). Builds 2006, 2007, 2008 failed at signing for the
  Watch complication only. What is left: on developer.apple.com, Identifiers, `au.com.pig.music.watchkitapp.widget`
  ("PigMusic Watch complication"): App Groups, Configure, tick `group.au.com.pig.music`, Save. Mark has not confirmed
  doing it. Then `git push origin main:refs/heads/testflight-YYYYMMDD-bN`; that build brings the complication AND the
  iPhone half of the queue limits. No TestFlight build from main can sign until then.
- **Reading Xcode Cloud and the identifiers (read-only):** a script on the Mac signs a token with the key that lives
  there (`~/.pigmusic-testflight.env`); it was `/tmp/asc_read.py` this session and may need recreating: modes `builds`,
  `issues <run>`, `logs <run>` (the export error is in `app-store-export-archive-logs/xcodebuild-export-archive.log`),
  `ids`. Registering identifiers is Mark's to do on the website.
- **Mark's device checks, still to come:** the changed items from his 20 Nov round; listening for #279; Shazam (#124:
  export diagnostics straight after a try); the two-day Watch delivery test once the complication is on his active face
  (#219 says what the export should show; whether the iPhone's push wakes the Watch depends on the watchOS version);
  memory (#253) after a day or two of use.
- **Retirement (#201), not done because the pass marks are not met (live counters 14–21 Nov):** Cover Art Archive
  covers and the cover-warming machinery (CAA still supplies 44.5% of covers; 17.3% of covers asked for never shown,
  mark 2%), and the fallback-profile ladder (mix downloads succeed 41.4%, mark 80%). Both figures deserve their own
  look. `remote-cache` on the live server (200 MB, 27 files) is unused; delete by hand with Mark's go-ahead.
- **Instant listening:** 102 of 144 taps played before D-130 (71%, target 90%). Read
  `scripts/performance_report.py --no-match` again after a few days to see what still misses.
- **Parked with the next design on the issue:** #266 (`batch-h-266-parked`), #270 (`batch-270-minimal-parked`), #261
  (`batch-k`).
- **Decisions:** D-121 to D-131. Next: D-132.
- **Lessons:** the Mac (`ssh mac`) runs the tap-through tests in ~15 minutes on the iOS Mark's phone has; one run at a
  time, one folder per branch; with its 8 GB a second booted simulator makes a run look hung. GitHub's runner is on
  iOS 26.5 and often times out. The Linux Swift compiler accepts code Xcode rejects. The image tests run outside the
  source tree: a test that reads a repository file must use `tests/source.py`'s `SOURCE`. Never chain verify and
  release in one background command. Codex outputs of this session: `.localdata/handover-20261121/`.

## Now

1. **The complication's app group** (Mark), then one TestFlight build from main; delete its branch when it arrives.
2. **Mark's device checks** (above).
3. **Read the no-match report** (#282) after a few days; fix #310.
4. **#266, #270, #261** from the designs on their issues, each its own batch.
5. **Covers and mix downloads:** why CAA still supplies 44.5% of covers and mix downloads succeed 41.4% (#201's
   remaining rows cannot be retired until these move).
6. **Live checks that close with use:** #272, #130, #142, #185, #188, #144/#145, #16/#17/#20/#22/#23/#54/#63 (#95).

## Next

Findings #295–#313; #312 (limits for downloads the server queues itself) needs a design. Then #226, #231, #225.

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
