# Playback

Playback is the part of PigMusic people notice first: a song must start quickly, keep playing through network hiccups, headphones and phone calls, and not drain the battery while paused. This note lists what must be protected when changing the players or the server's audio delivery, and the failures that taught each rule. The Watch has its own note: [watch-sync.md](watch-sync.md).

Each lesson cites the commit that introduced it; read the commit message for the reproduction.

## Must stay protected

Protect rapid Play/Pause, pending activation, interruptions, headphone removal and stopped-engine recovery with regressions and adversarial review. Remembering resume state must not require continuous paused execution. (Blueprint battery work; `41fcf9f`, `20ea50a`)

## iPhone paused and background behaviour

- Pausing stops the streaming-fallback clock (it ran every 250 ms after Pause) on pause, failure and end; paused background audio is released through a serial coordinator, stale activation completions are rejected, and the engine is acquired only after activation completes. Paused queue preparation does not start the engine. The queue and position are preserved and Play reactivates. (`41fcf9f`)
- A paused seek checkpoints and publishes its position explicitly, without restarting the fallback clock; otherwise paused seeks are left unsaved and unpublished. (`20ea50a`)
- Background playback still republishes progress/Now Playing at 10 Hz, and retained partial sources still poll while idle. Measure before reducing background publications or replacing partial-source idle polling. Correlate device request IDs with slow server responses before attributing network or background cost to them.
- The engine's ten-per-second reports must not rewrite Now Playing info; only real changes and seeks do. Checkpoints still use the exact position. (`b318f71`)
- Tests that check progress must await clock events, not scheduler deadlines; a three-second main-actor deadline failed on CI. (`3b74c20`)

## Interruptions, routes and the engine

- iOS can post an interruption "began" with no "ended" (seen after headphones came out). A person's Play (resume, remote Play) and music they choose (a new queue) end any interruption still standing via `PlaybackIntent.choosePlay()`; if another app really holds audio, activation fails and shows its error. An interruption marked `routeDisconnected` is a pause. Restarts of the same queue (fallback, mode change, retry) keep waiting for the interruption to end. (`d452c7c`)
- Keep 3 s scheduled ahead so main-thread work cannot starve output, and rebuild the engine on route changes only when it has actually stopped. (`5925486`)
- AVFoundation notifications (route, interruption, end of item, engine configuration) are delivered on the main queue before main-actor sinks run, and the Now Playing artwork handler is created nonisolated: MediaPlayer calls it on a background queue and Swift 6 isolation checks terminated the app. (`580ae03`)
- Playback refill wakes from buffer completions, not polling. (`facecf2`)

## Queues, restore and skipping

- A restored queue does not fetch its song at launch; it waits for Play, Next or Previous. After time away the connection can take about 20 s to wake, and a launch fetch held up the song chosen next. (`3e307e5`)
- `QueueStart` chooses the song and position first, so only the chosen song is fetched, and binds a saved position to one occurrence, taken by one preparation. (`00e54ec`)
- The 8 s slow-start fallback to streaming counts from the song's address arriving; before that the server is out of reach and streaming would only start the same wait again. (`3e307e5`)
- Only `NativeFailure.songUnavailable` (404, no stream matched, remote disabled) and `invalidMediaURL` pass over a song. Passing over any other failure let an unreachable server run through the whole queue in seconds. (`8bfaaef`)
- Seamless playback prepares the next viable song as soon as an upcoming acquisition fails, while the current song keeps playing. (`7a520fa`)

## Progressive transfer and decoding

- `ProgressiveDownload` fills a sparse temporary file over HTTP Range, fetching first where the decoder reads (a seek, or an MP4 index at the end). A following song fetches its opening 512 KB and then waits; a song skipped past stops fetching. `StreamingPCMDecoder` waits for missing bytes on its own queue; watchOS keeps the complete-file decoder. `RealtimeQueueSession` starts once 0.75 s is decoded and refills 2 s before playing on instead of stuttering. (`375d376`)
- A Request's stream termination and deinit cancel its data task, and every exit of a ranged transfer cancels the request. (`2d925c8`)
- Headerless VBR MP3s start from Core Audio's estimated length. An estimated duration never caps elapsed time or a seek; only an exact duration bounds a seek (`PlaybackSeek.target`). The decoder reports its length as estimated or exact, and a start past the real end ends the song at its measured length so the queue moves on. Markers no PCM block carried reach the ended snapshot. (`95819ca`, `00e54ec`, `625b0ef`, `2927213`)
- Players prefer the song's known length unless the stream's agrees. (`8708c8c`)
- CI tests check loading state, not wall-clock bounds, on shared runners. (`d2c584e`, `3c0e0ac`)

## Connections and sessions

- Connections kept through a break can be dead without the system knowing. API, artwork and song transfers use a `RenewableSession`, renewed when the app is active again after 30 s or more away; API requests time out after 20 s of silence so a safe request is retried on a new connection. (`820a4c9`)
- Never hand out a session that may be invalidated: callers borrow it with `withSession { }` for as long as it takes to create and start their task, and `ProgressiveDownload` starts each request with `dataTask(with:delegate:)` under the renewal lock. A replaced session is invalidated when its last borrower gives it back, with `finishTasksAndInvalidate` (not `invalidateAndCancel`) so running requests and bodies finish. A task created in an invalidated session is an uncatchable crash. (`a442739`, `6f57095`, `6d02fdf`)

## Server delivery of NAS files

- A playback address (`playable`) checks the snapshot's path by spelling only, with no NAS lookup; a missing file fails on open. (`f817343`)
- Nothing is cached per file. Each open or range request opens the file once with `O_NOFOLLOW`, asks the kernel where that descriptor really is (`/proc/self/fd` or `F_GETPATH`), requires it inside the resolved library root, and serves that same descriptor. A cached resolved path let a folder replaced by a symlink serve files from outside the library. (`28d2e86`)
- Each read opens the checked descriptor's own name (`/proc/self/fd/N`, `/dev/fd/N` on macOS), so nothing is allocated before AnyIO's checkpoint and a cancelled response leaks no descriptor. The checked descriptor closes when the response ends. (`9e98744`)
- `/api/v1/playback-urls` signs a page's songs in one query, and pages and the next 50 queued songs fetch addresses ahead. (`375d376`, `a442739`)
- Mix and radio playback rules (leases, `mix_lock`, stand-ins) are in [mixes.md](mixes.md#lessons-from-past-failures).

## Remote playback

Remote playback is experimental, never adds a file to the library, and never streams lossless (D-21).

- Instant sources (D-89, D-91): a tap asks JioSaavn and YouTube together; JioSaavn's copy wins when both have the song. FreeMp3Cloud and zvu4no are download sources only; their instant-listening relay was retired (#201). YouTube's search is YouTube Music's song search (`ytmusicapi`, through the guarded transport); its results pass the same song rule as JioSaavn's (`acceptable` in `server/remote.py`). The address comes from an isolated worker (`server/providers/youtube_worker.py`, yt-dlp's visionos client, AAC in MP4 only, no JavaScript runtime) and is tied to the server's network, so the server passes each range through (`YouTubeStream`, behind a token bound to the listener and a signed link that last 6 hours); a refused address is looked up afresh once. Video choices are kept 6 hours, addresses until 30 minutes before they expire. A JioSaavn no-match is remembered 30 minutes, so a song look-ahead found on YouTube does not wait for JioSaavn again.

- Only every source saying it does not have the recording is a miss (404, remembered 30 minutes). A timeout, a provider error or outage, or a CDN 408, 429 or 5xx is uncertain (503, remembered 2 minutes). Uncertainty must never become a false long song miss. (`43570dc`, `a98570d`, `5f4c0bc`, `8db8c20`)
- A JioSaavn match is kept 6 hours (1,000 at most, least recently used out). Older than 90 s, its address is re-checked with one 32-byte range request on the tap and looked up afresh only if the CDN no longer serves it. A song named by its release and recording is looked up as the catalogue knows it (ISRCs included), after the prepared-mix check, so a tap from any app shares one answer. (#192)
- Look-ahead (#192, `server/lookahead.py`): a release page, Home's feeds and a search (once typing has paused 1.5 s; its top two songs, matched first) match up to five songs a tap would stream, two at a time per person, as background metadata work: JioSaavn first (its own budget of two searches across everyone; taps keep their three slots, and a tap overtakes a look-ahead still waiting), then YouTube after a JioSaavn miss (one look-ahead at a time across everyone). A look-ahead's miss is not remembered. A new screen replaces what the last one queued and has not started; nothing runs while remote playback is off.
