# Verification beyond the automated suites

The commands for the three test tiers are in [AGENTS.md](../AGENTS.md#test-cheapest-first). Use disposable data and music fixtures. Never point tests at live appdata or the real NAS. Provider tests use mocked HTTP; FFmpeg tests decode real disposable audio. Each handover states which checks actually ran against which image. Earlier passes are not evidence for a new candidate.

## Behaviour the suites must keep covering

- Edition and recording conflicts, ambiguous matches, compilation and live context, identical-byte memberships.
- Edition anchors across scan order and restarts; edition-specific removal, cancellation, exclusions and restore.
- An idempotent persistent queue; claim cancellation and clear; interrupted publication; full decoding; quality replacement and fallback.
- Favourites without acquisition; playlist order, duplicates and missing files.
- Artist-job cancellation cannot submit work after cancelling.
- A missing or wrong NAS marker allows reads but creates no workspace or media paths.
- Default-deny routes, per-person scoping, signed media revocation, and provider rate limits (`tests/test_rate_limits.py`).

## Browser checks

Use local disposable fixtures for mutations, on desktop and at 375 px, in light, dark and system appearance, with keyboard focus and reduced motion.
- Fresh Import opens a chooser; a synthetic XML import starts a review without downloading; replacement import works.
- Artist and playlist links survive reload and back.
- The playlist picker handles focus and Escape.
- Player labels stay readable and actionable.
- Saved, queued, downloading and failed states are distinguishable.
- Admin options sit at the bottom of shared views.

## Performance

Mix acceptance uses a disposable library and cache: open Home on web and iOS/iPadOS 26+, confirm one horizontal row and a distinct Pig-Branding-based cover per mix kind where there is sufficient music, then play a ready mix end to end. Pause downloads and exhaust a small cache budget: keep the last ready edition playable and defer preparation. Rotate while a queue plays; a song shared by another member/mix must stay available. Add a discovery to the library while preparation is paused, resume publication, retire its mix and clean the cache: the library copy must remain. Save an edition, hide a song or edition and restart; metadata and feedback must persist. Expire a saved edition's cache, then play it with experimental streaming disabled: a standard cache download must restore playback without library membership. Check seedless regional charts, imported-but-unavailable taste seeds, bounded preparation resumption and day-to-day diversity. Verify real sources, several daily editions, Dynamic Type, VoiceOver, both appearances and large phone/tablet layouts before accepting mix quality. Server caching alone does not make music available when the phone is offline. Use the [isolated source-acceptance harness](operations.md#download-source-acceptance) with `--mix-cache` to verify real transfers without household appdata or NAS access.

For each measurement, record the dataset size, sample count and network location. Report warm cached reads separately from cold metadata, image or provider work, and use at least 30 samples for a p95 claim. Gates:
- cached local browsing: p95 under 100 ms;
- playlist cards: p95 under 150 ms;
- 50k-track artwork, delivery and two-person sync: under 1 GiB (CI repeats these in the image).

Never trade matching or validation guarantees for throughput.

```sh
.venv/bin/python scripts/benchmark_library.py
.venv/bin/python scripts/benchmark_api.py --url http://192.168.1.203:8765 --samples 30 --output .localdata/api-performance.json
.venv/bin/python scripts/benchmark_cover_store.py --sizes 10000 50000
.venv/bin/python scripts/benchmark_delivery.py
.venv/bin/python scripts/benchmark_native_sync.py
.venv/bin/python scripts/benchmark_mixes.py
```

`benchmark_api.py` sends GET requests only, to `/api/v1` paths, with an owner device token from `PIG_MUSIC_BENCHMARK_TOKEN`. `deploy_unraid.py` also runs `verify_state_migration.py` against a read-only copy of a stopped-state backup, and any migration failure aborts the release.

## Release acceptance checklist

One pass over every item in the blueprint's **Delivered** table, on the live release (`editions-20261021-v2`) and the rebuilt iPhone app 0.2.0 (5). Report failures by number (for example `D1`) with the device, OS, connection and what happened. The IDs in brackets are the roadmap items each check accepts.

**A. Web, desktop browser, signed in as the owner**

1. Open every Admin tab with the network log open: no 404s (U01-4).
2. Admin › Downloads and a library scan update live; background the tab and the network log shows no polling (R18).
3. Request a whole album: one Downloads card fills up; retry a failed song (R13).
4. Type an artist name quickly in Search: one catalogue request per pause, none on backspace (R14).
5. Pink Floyd and Arcade Fire: Library › Find more by… › catalogue and back; "In library" on a deluxe or remastered title; a live album's songs show as not in library and can be requested (R12, RV19, RV21).
6. Sign out and in as another person in the same tab, then search: nothing from the previous person. Reload offline: covers still show (RV19).
7. Sign in on two browsers and the phone: names, last-used times and "This device" are right; Sign out other devices from one (R15).
8. Admin › Server library › Duplicate songs: read the proposed list before moving anything (ED1).
9. Import a playlist whose songs are already on another edition: nothing downloads again (ED1).
10. New downloads show album covers on web and iPhone, including EPs and singles (ED1, LOG21).
11. Home: one row of mixes; open one, play it to the end, save an edition, hide a song. Check again over the next few days that the editions change and still sound coherent. Radio keeps going past the end of the queue. Admin shows mix preparation detail (MX0–MX4).
12. Now Playing's artist name opens the library artist page for library music and the full artist page for catalogue music (ED1).

**B. Internet access, iPhone on cellular with Wi-Fi off (NET)**

1. Sign in at `music.markrogers.au` in Safari and in the app.
2. Open an invite link on another device and join.
3. Enter a wrong password repeatedly: the lockout message appears and clears after the wait.

**C. iPhone: launch and library**

1. Install over the old build and launch: the splash shows only until the first refresh, with no flash (N14A). Offline, the cached library shows no raw sync errors (FB19).
2. With 500+ downloads, a sync is quick and covers don't flicker (EFF1).
3. Pick Soundtracks during a pull-to-refresh: it loads (RV19).
4. Open an album and a playlist twice: the server log shows `304` the second time (EFF1).
5. A Search artist opens the full artist page with a library link; the graphical artist page and photo fade look right with and without Fanart (N14A, N14D, AUD1).
6. Library artist page: Play, Shuffle, Songs and "Find more by…" (R12). A requested album shows in full with pending status graphics (N14B).
7. Open a Recommended album cold: it draws at once (FB19). Home prefetches no albums on cellular, Low Data Mode or Low Power Mode (EFF1).
8. Account › Devices and sessions (R15).
9. Change appearance quickly several times, offline and online: the last choice wins and survives a sync (ED1, RV20).
10. Confirmation dialogs (delete playlist, remove music, artist request) open at their button (ED1).

**D. iPhone: playback**

1. A playlist mixing device, server and missing songs on the seamless engine: missing songs are passed over, including one found missing only after the previous song ended (it plays the replacement once) (ED1, RV20, RV21).
2. Another member's household song plays without adding it (HH, AUD4).
3. A downloaded FLAC over 128 MB plays; on a slow connection playback falls back to streaming (AUD6).
4. Remote playback of a song nobody has (R01).
5. Headphone and lock-screen controls; playback over poor reception (AUD1, FB19).
6. A mix and the radio: the radio appends without a glitch (MX2, MX4).
7. One YouTube, one SoundCloud and one Monochrome download each play (SP).

**E. iPhone: downloads**

1. Downloads › Server shows one card per request; Clear finished works (R13, N14C).
2. On this device groups by album and playlist; a song in both stays until it is removed from both (N14C).
3. A playlist with missing songs offers "Add N missing songs to server" (FB19).
4. With downloads queued on cellular, change the cellular setting: transfers restart under the new setting, also after a relaunch (ED1).
5. Background and foreground on cellular; lock the phone mid-transfer (FB19).

**F. Watch Ultra 3 (and Series 7)**

1. Send a playlist and an artist; lock the phone mid-send; send an empty playlist, then add songs to it on the phone: the Watch follows (W02, AUD3).
2. Reinstall the Watch app: the phone shows activation, pairing and installation correctly (AUD3).
3. Interrupt a sync; cold-launch with the phone off; disconnect and reconnect AirPods; relaunch: the queue comes back paused (AUD2).
4. Play restarts a finished queue; Crown volume and the output picker work (AUD1, AUD8).
5. The Watch controls the phone's radio (MX4).
6. The battery run in step 5 of [Native device acceptance](#native-device-acceptance) (AUD8).

**G. Other**

1. iPad: repeat C1, C5 and D1 in both orientations.
2. Open `swift/PigMusic.xcodeproj` in Xcode: no "Update to recommended settings" prompt (XPROJ).
3. After any failure: Account › Diagnostics › Export diagnostics (AUD7).

## Native device acceptance

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

## Live playback diagnostics

Use these for playback that is slow or patchy on the real server. All three read through a **dedicated test member** (invite one, add a few songs to its library; adding never downloads). Never use a household account, and never put the token in chat, Git or a command line.

1. **Request IDs and server timing (always on).** Every response carries `X-Request-ID` and `Server-Timing` (`app` is the server's total; `auth-ready`, `auth-session`, `auth-link`, `auth-mix`, `auth-media` are sign-in; `handler` is when a sync endpoint got a worker thread; `mix-entry`, `mix-lock`, `mix-lease`, `mix-file`, `stand-in`, `file` are the playback lookup). The server logs every playback address and anything slower than 1 s, with how busy its worker pools were (`pig_music.timing`), and warns when the event loop was blocked or a pool had a queue. The iPhone's diagnostics show the same ID and the server's share: `[network] playback_url 200 in 16211 ms, server 16100 ms: mix-lease 16050 (id …)`. To match a phone line on the server:
   ```sh
   ssh root@192.168.1.235 "docker logs --since 24h PigMusic 2>&1 | grep <id>"
   ```
   A large client time with a small `server` is the connection; a large `handler` with small phases is a worker-thread queue; a large `auth-*` or `mix-lock`/`mix-lease` is the server's own wait.
2. **Probe** (Python, any machine): sign in once, which asks for the password and keeps only the token, private to you:
   ```sh
   .venv/bin/python -m scripts.probe_playback login --server https://music.markrogers.au --username <test member> --token-file ~/.config/pigmusic/probe-token
   .venv/bin/python -m scripts.probe_playback run --server https://music.markrogers.au --token-file ~/.config/pigmusic/probe-token --output probe.jsonl
   ```
   Each song: its address, the file's opening (`Range: bytes=0-`, as playback asks) and a read near its end, each with the server's timing and ID; `--full` also times whole files. `--repeat 0 --interval 300` keeps going as a canary. It changes nothing beyond the test member's own mix leases.
3. **Harness** (the phone's own playback code on this Mac): the API client with its retries, `AudioSourcePreparer`, `PlaybackSourceRepository` and `QueuePCMStream`, wired as the seamless engine is. No sound: it decodes as fast as bytes arrive and reports each song's address, ready time, first audio and any silence a player would have had, then the run's diagnostics lines:
   ```sh
   swift run --package-path swift/PigKit PigPlaybackHarness --server https://music.markrogers.au --token-file ~/.config/pigmusic/probe-token --mix-songs 5 --library 3
   ```
   It proves transfers, decoding and queue timing over the real route, not the audio session, interruptions or background behaviour, which need a device.
