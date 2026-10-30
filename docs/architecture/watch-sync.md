# Watch sync

The Apple Watch plays music stored on the Watch itself. The paired iPhone chooses the songs, fetches Watch-sized copies from the server and sends them across; the Watch never talks to the server or holds a password. This note lists what must be protected when changing that delivery, and the failures that taught each rule.

Each lesson cites the commit that introduced it; read the commit message for the reproduction. Playback rules shared with the iPhone are in [playback.md](playback.md).

## Ground rules

- Watch music lives on the Watch, prepared by the paired phone; the Watch never receives server credentials (D-16). The Watch remains phone-driven: do not change independent-app flags (`WKRunsIndependentlyOfCompanionApp`) as incidental cleanup (D-33).
- Playlists sent to the Watch update automatically (D-20). Long-term Watch availability requires eligible durable songs; there are no direct dynamic Watch mixes.
- Watch decisions belong in PigCompanion (PigKit), where they are unit-tested: following sent playlists, whether a queue keeps playing through a new manifest, connection wording and the readiness summary. `WatchModel` and the phone's Watch controller are app code outside PigKit and have no unit tests. (`57e6a92`)
- Before another resync fix, inspect fresh Watch receipts, context and transfer logs; transfer completions after reconnects do not by themselves establish a full-audio redelivery cause.

## Manifests and the committed library

- The last applied manifest is the committed Watch library. A new revision for the same account is staged; its songs stay playable until the matching manifest replaces them, including across a relaunch or a phone disconnect mid-sync. An account change still revokes at once. Playback only stops when a queued song is removed or changed. (`f5c63df`)
- A library manifest (up to 2 MB of JSON) is read and decoded off the Watch's main actor, in a detached task; decoding on the main actor stalled the Watch while it was playing. (`631094e`)
- A superseded manifest never stops playback. A context is noted on the main actor the moment it arrives, before applying it suspends (`CompanionContextFence`), and a manifest is admitted only if no newer context was announced, checked after the last await before playback may stop. A recheck after the reads was not enough: it was a snapshot. (`3965928`, `2119c4c`)
- A damaged saved manifest starts empty without crashing and recovers on the next manifest; a full Watch budget rejects more audio and keeps what is there; partial files from an interrupted save are removed. (`57e6a92`)

## Context and confirmations

- The phone publishes the context only when it changed (or on force/reconnect), and the Watch only confirms a repeat (`CompanionContext.arrival`: apply / repeated / stale). Publishing after every confirmation caused a context ping-pong of hundreds of identical revisions a minute. (`8bfaaef`)
- Reconnects retain confirmations instead of forcing manifests. (`facecf2`)
- The phone keeps the Watch's last receipt and restores it on launch when it matches the current library context, so a relaunch does not count every Watch song as still to send. (`7b7bb3e`)
- Each inbox file is delivered once at a time; a file already taken is ignored. (`8bfaaef`)

## Transfers

- In-flight songs are kept across library versions for the same account (the Watch accepts earlier tickets for songs it still lists); only superseded manifests and songs that left are cancelled. Songs are de-duplicated by song and checksum, not by version. While a batch is preparing, a new version goes out at most every 45 s (removals and Retry go at once). Old manifest files are kept a few versions longer than their transfers. Before this, every finished song made a new version that cancelled and re-queued every transfer. (`8708c8c`)
- WatchConnectivity keeps outstanding file transfers across launches with their original URLs. After an app update moves the data container they fail, so cancel queued transfers whose file can no longer be read; otherwise the phone skips those songs forever. (`3e6ef94`)
- WatchConnectivity reply handlers must not inherit main-actor isolation; that trapped at runtime. (`5925486`)
- Watch AAC copies download through their own background URL session, so preparation continues while the phone is locked; leaving the foreground suspends only server polling. (`8988f40`)
- Sent playlists are subscriptions, so an emptied playlist keeps following and refills. Songs removed from a followed playlist on the Watch are remembered per playlist until the song leaves the playlist or it is sent again. (`8988f40`, `b318f71`)

## Server device copies

- The phone asks for the waiting copies (up to 32) as soon as a run starts; each copy's own turn asks again. Cancelling a batch also cancels the one it waits for, and the controller owns, cancels and waits for the ahead-requests when its worker stops. (`96e3c76`, `41f9dee`, `9f681d9`)
- The server converts two copies at once, each claiming its job atomically under the cache lock; pruning, publishing and recording a ready copy are one step (`DeviceCopies.admit`) so two finishing conversions cannot both pass the quota. Conversion waits only while a download is validating or importing, not while one transfers. (`96e3c76`, `41f9dee`)
- Encoder settings stay native AAC 256 kbps followed by a full decode check: a different coder would change every cached copy's key and its quality. (`96e3c76`)

## Battery and main-thread work

- The paused Watch stops its engine and clock, preserves its recovery position, and suppresses elapsed-only background updates. (`facecf2`)
- Diagnostics logging must not run on the main thread: the caller only takes the time, and formatting, redaction and appending happen on the log's queue, with the file written at most every half second and at once in the background. (`fea759f`)
- Route and engine-configuration changes are debounced and rebuild output only when the engine actually stopped; a media-services reset keeps the queue and position; Play never rebuilds a running engine. The Watch queue and position are restored paused after a relaunch, with audio prepared on the first Play. (`f5c63df`)
