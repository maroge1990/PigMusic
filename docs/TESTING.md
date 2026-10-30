# Testing

This page explains how a PigMusic change is checked before the household relies on it: which kind of test proves what, who runs it, and the step-by-step checks for real phones and watches.

Commands for the automated suites are in [AGENTS.md](../AGENTS.md#test-cheapest-first). Release, recovery and the server's layout are in [OPERATIONS.md](OPERATIONS.md); what is planned or in progress is in the [roadmap](../ROADMAP.md).

## Tiers and who runs them

| Tier | What | Who | Required for |
| --- | --- | --- | --- |
| T1 Automated | pytest, web unit tests, Playwright against a mocked API, PigKit `swift test`, the Swift contract fixtures, unsigned iOS and watchOS builds | Claude | Every functional commit |
| T2 Real media, offline | FFmpeg fixture decoding, `PigAudioProbe`, `PigPlaybackHarness` and the probe against a local server with a disposable library, benchmarks | Claude | Any change to playback, downloads, matching, delivery or performance |
| T3 End to end | A staging PigMusic test container on Unraid with a disposable library and fake external services, driven by Claude (the browser pane for the web app) | Claude | Any change a user can see or touch, **once the staging container exists** |
| T4 Deployed, read-only | Health, version, logs and live playback diagnostics through a dedicated test member | Claude | Diagnosing a live report |
| T5 Owner devices | Physical iPhone, iPad and Watch; the release acceptance checklist; native device acceptance; deploy | Mark | Release acceptance and anything no other tier can settle |

- **T3 runs on the Unraid rig** (see T3 below); fake download sources are not built yet, so checks that need a real transfer still fall to T5.
- **Machines:** the development Mac has 8 GB of memory and cannot run the iOS simulator. Native screen checks are therefore T5, and native playback is covered by PigKit harness runs (T2 locally, T4 against the live server).
- **Claude's default is to test, not to ask.** T1 to T4 touch no household data and change nothing on the live server, so Claude runs them without asking. Anything that changes production (a release, a template, a setting, the household library) needs Mark's yes; see [Releasing](OPERATIONS.md#releasing).
- **Status words:** *Implemented* (code done) → *Locally verified* (T1 to T3 pass, evidence linked) → *Verified* (Mark's T5 report). Only Mark writes *Verified*. An implemented change stays open until its acceptance gate passes.
- **Mark's checklist** in each handoff lists only T5 items: numbered steps, each with the expected result and what to report (server revision, app build, device, OS, connection).
- Every functional change has a test that **fails on the old code**; show it. An acceptance line in an issue names the tier that proves the fix.

### Rules for every tier

- Use disposable data and music fixtures, with `PIG_MUSIC_DATA` and `PIG_MUSIC_LIBRARY` set explicitly. Never point tests at live appdata or the real NAS.
- Provider tests use mocked HTTP; FFmpeg tests decode real disposable audio.
- Each handover states which checks actually ran against which image. Earlier passes are not evidence for a new candidate.
- Review evidence must say what could not be reproduced.

## Evidence

Small, public-safe text evidence only, in `docs/evidence/YYYY-MM-DD-slug/`: before/after outputs, summaries, measurements and screenshots. Written once, linked from an issue or commit. Raw logs, diagnostics exports, audio captures, tokens and anything naming household hosts stay out of Git (local disk), referenced by path and SHA-256 in the folder's README.

## T1: Automated

The commands, in order of cost, are in [AGENTS.md](../AGENTS.md#test-cheapest-first): the backend suite, the web unit tests, Playwright against a mocked API, formatting and OpenAPI checks, the brand checks, PigKit `swift test`, the two Swift contract tests against `scripts.serve_contract_fixture`, and the unsigned iOS and watchOS builds. CI repeats them on push.

### Behaviour the suites must keep covering

- Edition and recording conflicts, ambiguous matches, compilation and live context, identical-byte memberships.
- Edition anchors across scan order and restarts; edition-specific removal, cancellation, exclusions and restore.
- An idempotent persistent queue; claim cancellation and clear; interrupted publication; full decoding; quality replacement and fallback.
- Favourites without acquisition; playlist order, duplicates and missing files.
- Artist-job cancellation cannot submit work after cancelling.
- A missing or wrong NAS marker allows reads but creates no workspace or media paths.
- Default-deny routes, per-person scoping, signed media revocation, and provider rate limits (`tests/test_rate_limits.py`).

## T2: Real media, offline

- **FFmpeg fixture decoding:** the backend suite decodes real disposable audio when FFmpeg is installed (for example `tests/test_downloader.py`).
- **Apple codecs and transitions:** `scripts/probe_native_audio.py` renders generated tones through a built `PigAudioProbe` (`PigAudioProbe [--bounded] first-audio second-audio output.wav crossfade-seconds`). It never reads household music or providers.
- **Phone playback code:** `PigPlaybackHarness` (see [Live playback diagnostics](#live-playback-diagnostics) for what it does) and `scripts.probe_playback` against a local server with a disposable library and a test member. The probe accepts only an HTTPS address, so the local server must be reached over HTTPS.
- **Download sources:** the isolated [download-source acceptance harness](OPERATIONS.md#download-source-acceptance) verifies real transfers without household appdata or NAS access.

### Performance

Mix acceptance uses a disposable library and cache: open Home on web and iOS/iPadOS 26+, confirm one horizontal row and a distinct Pig-Branding-based cover per mix kind where there is sufficient music, then play a ready mix end to end. Pause downloads and exhaust a small cache budget: keep the last ready edition playable and defer preparation. Rotate while a queue plays; a song shared by another member/mix must stay available. Add a discovery to the library while preparation is paused, resume publication, retire its mix and clean the cache: the library copy must remain. Save an edition, hide a song or edition and restart; metadata and feedback must persist. Expire a saved edition's cache, then play it with experimental streaming disabled: a standard cache download must restore playback without library membership. Check seedless regional charts, imported-but-unavailable taste seeds, bounded preparation resumption and day-to-day diversity. Verify real sources, several daily editions, Dynamic Type, VoiceOver, both appearances and large phone/tablet layouts before accepting mix quality. Server caching alone does not make music available when the phone is offline. Use the [isolated source-acceptance harness](OPERATIONS.md#download-source-acceptance) with `--mix-cache` to verify real transfers without household appdata or NAS access. (The iOS, real-source and accessibility parts of this are T5 until T3 exists.)

For each measurement, record the dataset size, sample count and network location. Report warm cached reads separately from cold metadata, image or provider work, and use at least 30 samples for a p95 claim. Gates:
- cached local browsing: p95 under 100 ms;
- playlist cards: p95 under 150 ms;
- 50k-track artwork, delivery and two-person sync: under 1 GiB (CI repeats these in the image).

Never trade matching or validation guarantees for throughput.

For event-loop blocking (#16) and lock-hold (#17) windows, exclude only the first 60 s after startup. Subsequent 30-minute gates need actual download/mix/export load; cached, small-library or Mac-only tests must be labelled as such.

```sh
.venv/bin/python scripts/benchmark_library.py
.venv/bin/python scripts/benchmark_api.py --url http://<pigmusic-lan-address>:8765 --samples 30 --output .localdata/api-performance.json
.venv/bin/python scripts/benchmark_cover_store.py --sizes 10000 50000
.venv/bin/python scripts/benchmark_delivery.py
.venv/bin/python scripts/benchmark_native_sync.py
.venv/bin/python scripts/benchmark_mixes.py
```

`benchmark_api.py` sends GET requests only, to `/api/v1` paths, with an owner device token from `PIG_MUSIC_BENCHMARK_TOKEN`. `deploy_unraid.py` also runs `verify_state_migration.py` against a read-only copy of a stopped-state backup, and any migration failure aborts the release. Placeholders such as `<pigmusic-lan-address>` are explained in [OPERATIONS.md](OPERATIONS.md#household-topology).

## T3: End to end

T3 runs a staging PigMusic on Unraid (D-53) with a disposable generated library and fake metadata services, driven by `scripts/test_rig.py` (#15):

```sh
.venv/bin/python -m scripts.test_rig up --image ghcr.io/maroge1990/pig-music@sha256:…   # a digest only, never a tag
.venv/bin/python -m scripts.test_rig check     # owner, 60 generated songs, scan, Home mixes
.venv/bin/python -m scripts.test_rig artist    # a cold artist page: seconds, songs, albums, fake requests (#21)
.venv/bin/python -m scripts.test_rig mixes     # Home mixes p50/p95 over a minute (#22)
.venv/bin/python -m scripts.test_rig status | down | reset
```

- **What runs:** `pig-music-test-app` (2 CPUs, 2 GB) and `pig-music-test-fake` (`scripts/fake_services.py`), both `--restart no`, on an internal Docker network with no internet access, with data only under `appdata/pig-music-test`. Nothing is published to the LAN; `scripts/rig_driver.py` runs inside the app container against 127.0.0.1.
- **Fakes:** `PIG_MUSIC_TEST_SERVICES` (`server/test_services.py`) sends the server's MusicBrainz, ListenBrainz, Cover Art Archive, Apple chart and artist-image requests to the fake and refuses every other host. The fake answers deterministically; `POST /_control` sets `delay`, `fail` and `albums`, and `GET /_stats` counts requests.
- **Not faked yet:** download sources. The engine runs without connecting, so every search finds nothing (the overnight "unfindable" case); song requests through a fake source are the next step.
- **Image:** `up` refuses an image without `server/test_services.py`. Avoid running scenarios around a scheduled release (00:05–01:00 Melbourne) so live measurements stay clean.
- The rig never touches the production container, its appdata, its image tag or the NAS; `status` reads production read-only.

The browser checks below still run against a local server with disposable fixtures.

The iOS simulator cannot run on the 8 GB development Mac, so native screens are checked on devices (T5) and native playback through PigKit harness runs.

### Browser checks

Use local disposable fixtures for mutations, on desktop and at 375 px, in light, dark and system appearance, with keyboard focus and reduced motion.
- Fresh Import opens a chooser; a synthetic XML import starts a review without downloading; replacement import works.
- Artist and playlist links survive reload and back.
- The playlist picker handles focus and Escape.
- Player labels stay readable and actionable.
- Saved, queued, downloading and failed states are distinguishable.
- Admin options sit at the bottom of shared views.

## T4: Deployed, read-only

Claude may read the live server's state without asking: `/api/health` at `<public-origin>` or `<tailnet-origin>`, the running image's OCI revision label, container logs on `<unraid-host>`, and the diagnostics below. None of this changes anything. The placeholders and where their real values live are listed in [OPERATIONS.md](OPERATIONS.md#household-topology).

### Live playback diagnostics

Use these for playback that is slow or patchy on the real server. The probe and harness read through a **dedicated test member** (invite one, add a few songs to its library; adding never downloads). Never use a household account, and never put the token in chat, Git or a command line.

1. **Request IDs and server timing (always on).** Every response carries `X-Request-ID` and `Server-Timing` (`app` is the server's total; `auth-ready`, `auth-session`, `auth-link`, `auth-mix`, `auth-media` are sign-in; `handler` is when a sync endpoint got a worker thread; `mix-entry`, `mix-lock`, `mix-lease`, `mix-file`, `stand-in`, `file` are the playback lookup). The server logs every playback address and anything slower than 1 s, with how busy its worker pools were (`pig_music.timing`), and warns when the event loop was blocked or a pool had a queue. A loop stuck for 1 s or more also logs where it is stuck (`event loop stuck for … at:` with the loop's stack and any worker thread in server code), every 5 s while it lasts; nothing on the server answers during such a stall. The iPhone's diagnostics show the same ID and the server's share: `[network] playback_url 200 in 16211 ms, server 16100 ms: mix-lease 16050 (id …)`. To match a phone line on the server:
   ```sh
   ssh root@<unraid-host> "docker logs --since 24h PigMusic 2>&1 | grep <id>"
   ```
   A large client time with a small `server` is the connection; a large `handler` with small phases is a worker-thread queue; a large `auth-*` or `mix-lock`/`mix-lease` is the server's own wait.
2. **Probe** (Python, any machine): sign in once, which asks for the password and keeps only the token, private to you:
   ```sh
   .venv/bin/python -m scripts.probe_playback login --server <public-origin> --username <test member> --token-file ~/.config/pigmusic/probe-token
   .venv/bin/python -m scripts.probe_playback run --server <public-origin> --token-file ~/.config/pigmusic/probe-token --output probe.jsonl
   ```
   Each song: its address, the file's opening (`Range: bytes=0-`, as playback asks) and a read near its end, each with the server's timing and ID; `--full` also times whole files. `--repeat 0 --interval 300` keeps going as a canary. It changes nothing beyond the test member's own mix leases.

   Other scenarios take the same `--server`, `--token-file` and optional `--output`, and each ends with how often the server's event loop stalled while it ran:
   - `remote`: songs not in the household, from online sources, asked twice (the second shows the relay cache), with each source's search and transfer time (`jiosaavn-miss`, `youtube-found`, `youtube-transfer`, `relay-cache`). `--song "Artist|Title|Album|Seconds"` picks songs; household songs are skipped.
   - `idle`: one song warm, then after each `--gap` seconds (default 60, 300 and 900) on the same connections.
   - `browse`: the app's screens and library sync, each opened twice.
   - `artwork`: the share of library, Home and artist artwork that is real, then again after `--settle` seconds (the server fetches missing art when asked).
   - `watch`: what the phone does to send songs to the Watch: asks for a device copy (`--quality aac` by default, converted on the server), waits for it, downloads it and checks it against the advertised SHA-256.
   - `server`: the probe member's recent slow requests and the server's stalls, from `/api/v1/diagnostics`. The owner's `/api/v1/admin/diagnostics` adds everyone's requests, each route's p50/p95 and the stall stacks.
3. **Diagnostics analyser** (an exported diagnostics file, iPhone and Watch logs together): time to sound by kind, slow requests with their IDs, queues that emptied themselves, refresh failures, Watch delivery rate, sync loops, account mismatches and crash reports, as plain findings.
   ```sh
   .venv/bin/python -m scripts.analyse_diagnostics PigMusic-diagnostics.txt --since 2026-10-26T08:00+11:00
   ```
4. **Harness** (the phone's own playback code on this Mac): the API client with its retries, `AudioSourcePreparer`, `PlaybackSourceRepository` and `QueuePCMStream`, wired as the seamless engine is. No sound: it decodes as fast as bytes arrive and reports each song's address, ready time, first audio and any silence a player would have had, then the run's diagnostics lines:
   ```sh
   swift run --package-path swift/PigKit PigPlaybackHarness --server <public-origin> --token-file ~/.config/pigmusic/probe-token --mix-songs 5 --library 3
   ```
   It proves transfers, decoding and queue timing over the real route, not the audio session, interruptions or background behaviour, which need a device.

## T5: Owner devices

Test devices: iPhone 16 Pro Max (iOS 27), iPhone 12, Apple Watch Ultra 3 (watchOS 27) and Series 7. For a device run, note the server revision, the app build, the device, OS and connection; keep tokens and links out of anything shared.

### Release acceptance checklist

Use this checklist for release acceptance, on the server revision and native builds actually under test; record those versions in the result. Status and sequencing live only in the [roadmap](../ROADMAP.md). Report failures by number (for example `D1`) with the device, OS, connection and what happened. The IDs in brackets map checks to roadmap work; this procedure is not a separate backlog.

**A. Web, desktop browser, signed in as the owner**

1. Open every Admin tab with the network log open: no 404s (#36).
2. Admin › Downloads and a library scan update live; background the tab and the network log shows no polling (#36).
3. Request a whole album: one Downloads card fills up; retry a failed song (#36).
4. Type an artist name quickly in Search: one catalogue request per pause, none on backspace (#36).
5. Pink Floyd and Arcade Fire: Library › Find more by… › catalogue and back; "In library" on a deluxe or remastered title; a live album's songs show as not in library and can be requested (#36, #37).
6. Sign out and in as another person in the same tab, then search: nothing from the previous person. Reload offline: covers still show (#37).
7. Sign in on two browsers and the phone: names, last-used times and "This device" are right; Sign out other devices from one (#36).
8. Admin › Server library › Duplicate songs: read the proposed list before moving anything (#37).
9. Import a playlist whose songs are already on another edition: nothing downloads again (#37).
10. New downloads show album covers on web and iPhone, including EPs and singles (#37).
11. Home: one row of mixes; open one, play it to the end, save an edition, hide a song. Check again over the next few days that the editions change and still sound coherent. Radio keeps going past the end of the queue. Admin shows mix preparation detail (#33).
12. Now Playing's artist name opens the library artist page for library music and the full artist page for catalogue music (#37).

**B. Internet access, iPhone on cellular with Wi-Fi off (#39)**

1. Sign in at `<public-origin>` in Safari and in the app.
2. Open an invite link on another device and join.
3. Enter a wrong password repeatedly: the lockout message appears and clears after the wait.

**C. iPhone: launch and library**

1. Install over the old build and launch: the splash shows only until the first refresh, with no flash (#35). Offline, the cached library shows no raw sync errors (#35).
2. With 500+ downloads, a sync is quick and covers don't flicker (#28).
3. Pick Soundtracks during a pull-to-refresh: it loads (#37).
4. Open an album and a playlist twice: the server log shows `304` the second time (#28).
5. A Search artist opens the full artist page with a library link; the graphical artist page and photo fade look right with and without Fanart (#32, #35).
6. Library artist page: Play, Shuffle, Songs and "Find more by…" (#36). A requested album shows in full with pending status graphics (#35).
7. Open a Recommended album cold: it draws at once (#35). Home prefetches no albums on cellular, Low Data Mode or Low Power Mode (#28).
8. Account › Devices and sessions (#36).
9. Change appearance quickly several times, offline and online: the last choice wins and survives a sync (#37).
10. Confirmation dialogs (delete playlist, remove music, artist request) open at their button (#37).

**D. iPhone: playback**

1. A playlist mixing device, server and missing songs on the seamless engine: missing songs are passed over, including one found missing only after the previous song ended (it plays the replacement once) (#37).
2. Another member's household song plays without adding it (#32).
3. A downloaded FLAC over 128 MB plays; on a slow connection playback falls back to streaming (#32).
4. Remote playback of a song nobody has (#34).
5. Headphone and lock-screen controls; playback over poor reception (#32, #35).
6. A mix and the radio: the radio appends without a glitch (#33).
7. One YouTube, one SoundCloud and one Monochrome download each play (#34).

**E. iPhone: downloads**

1. Downloads › Server shows one card per request; Clear finished works (#35, #36).
2. On this device groups by album and playlist; a song in both stays until it is removed from both (#35).
3. A playlist with missing songs offers "Add N missing songs to server" (#35).
4. With downloads queued on cellular, change the cellular setting: transfers restart under the new setting, also after a relaunch (#37).
5. Background and foreground on cellular; lock the phone mid-transfer (#35).

**F. Watch Ultra 3 (and Series 7)**

1. Send a playlist and an artist; lock the phone mid-send; send an empty playlist, then add songs to it on the phone: the Watch follows (#29).
2. Reinstall the Watch app: the phone shows activation, pairing and installation correctly (#29).
3. Interrupt a sync; cold-launch with the phone off; disconnect and reconnect AirPods; relaunch: the queue comes back paused (#29).
4. Play restarts a finished queue; Crown volume and the output picker work (#29, #32).
5. The Watch controls the phone's radio (#33).
6. The battery run in step 5 of [Native device acceptance](#native-device-acceptance) (#29).

**G. Other**

1. iPad: repeat C1, C5 and D1 in both orientations.
2. Open `swift/PigMusic.xcodeproj` in Xcode: no "Update to recommended settings" prompt (#38).
3. After any failure: Account › Diagnostics › Export diagnostics (#30).

### Native device acceptance

Simulator builds and PCM captures don't prove device behaviour. For each finding, record the device, OS, connection, playback mode, format and exact failure.

1. **Phone and tablet:** install, update and reopen. Check:
   - owner/member separation and provider fallback;
   - large text and VoiceOver;
   - search and paging at 50k;
   - playlists, import review, settings, edit conflicts and replay after an uncertain response.
2. **Playback:**
   - continuous-album and crossfade boundaries with real AAC, MP3, FLAC and mixed-rate sources;
   - repeat, pause/seek and lock-screen controls;
   - interruptions, Bluetooth/AirPlay, background timing and storage pressure;
   - streaming fallback.
3. **Downloads:**
   - original and AAC transfers, then background, lock and reopen;
   - cellular policy, interrupted resume and relaunch;
   - low storage, quality or source replacement, and confirmed revocation.

   An outage alone must keep files. A confirmed access change must stop playback and remove access.
4. **Watch Ultra 3:**
   - push from the phone and request from the Watch;
   - lower the wrist during delivery, then disconnect and retry;
   - remove and redownload;
   - turn the phone off and play through headphones (gapless, crossfade, repeat, seeks);
   - reconnect and verify listening history, access reconciliation and user switching.
5. **Watch battery on a run:** charge to 100%, send at least two hours of music, turn the phone off and Watch Wi-Fi/cellular off, start a workout with Bluetooth headphones and play for 60 minutes with the screen asleep. Record the start and end battery and the audio format. Compare with the same run using Apple Music downloads. Anything above about 10% per hour needs profiling with Instruments (Energy Log) before it is called acceptable.
6. **Diagnostics after a failure:** on iPhone, Account › Diagnostics › Ask the Watch for its log, then Export diagnostics. The file holds both devices' lifecycle logs and any crash reports, with no tokens or links.
7. **Server:** match server state and provider acquisition on an isolated Unraid library copy, with a backup.

Debug builds accept `--demo` and `--seamless-demo` for a synthetic library; remove them for real acceptance.
