# PigMusic architecture

PigMusic runs as one container on the household's Unraid server. It keeps its own records in a small database, treats the music on the NAS as the real library, and serves a web app plus iPhone, iPad and Watch apps. The sections below are for whoever changes the code: where each area lives, the rules that keep the library consistent, and what to read before touching the riskiest areas.

Product rules are in [PRODUCT.md](PRODUCT.md), the reasons behind them in [DECISIONS.md](DECISIONS.md), and build, test and review rules in [AGENTS.md](../AGENTS.md).

## Overview

One Unraid container supervises FastAPI and a pinned internal slskd. FastAPI serves the React/Vite bundle. SQLite owns the durable queue, acquired records, playlists, preferences, accounts and metadata documents. Appdata holds the snapshot, private connection files, artwork and operation journals. NAS music is the source of truth; scanning refreshes the projection. There is no filesystem watcher, so use Scan library after external changes.

| Area | Source |
| --- | --- |
| HTTP composition, provider lifetime | `server/app.py`, `server/routes/` |
| Durable state, queue claims, library refresh | `server/store.py`, `server/migrations/` |
| Album grouping, Apple XML import; classification | `server/library.py`; `server/releases.py` |
| Contextual matching | `server/matching.py` |
| MusicBrainz cache and rate limit | `server/catalogue.py` |
| Acquisition, validation and tagging; sources | `server/downloader.py`, `server/providers/`, `server/soulseek.py`, `server/vendor/` |
| Quality and replacement | `server/quality.py` |
| NAS boundaries, publication, recovery, organisation | `server/storage.py`, `server/exporter.py`, `server/recovery.py`, `server/organisation.py` |
| Scanning | `server/scanner.py` |
| Accounts, security, transport | `server/auth.py`, `server/security.py`, `server/transport.py` |
| Personal libraries, removal | `server/user_library.py`, `server/personal_removal.py` |
| Playlists, favourites | `server/playlists.py` |
| Playback, remote resolution, device AAC copies | `server/media.py`, `server/remote.py`, `server/device_copies.py` |
| Artwork, enrichment, pre-cache | `server/artwork.py`, `server/cover_store.py`, `server/enrichment.py`, `server/warmer.py`, `server/popularity.py` |
| Web client | `client/src/` (`api.ts`, `AppShell.tsx`, `SongRow.tsx`, `Player.tsx`) |
| Native | `swift/PigKit` (API, store, sync, playback, downloads), `swift/PigMusic`, `swift/PigMusicWatch`; contract `api/` ↔ `swift/PigKit/Sources/PigAPI/openapi.json` |
| Runtime supervision | `server/runtime.py` |
| Mixes, radio and their cache | `server/mixes.py`, `server/mix_cache.py`, `server/home.py` |

## Identity invariants

Snapshot track IDs stay stable, album edition anchors keep album IDs on their first known edition, removal exclusions are edition-aware, and queue workers publish only under their own claim token. Change none of these without migration fixtures and a backed-up production-state comparison. Full rules and the lessons behind them: [architecture/library-identity.md](architecture/library-identity.md).

## Server-wide rules

- **The event loop never waits.** SQLite reads and writes, NAS marker reads, path resolution, stats and lock acquisition run in worker threads, never on the event loop; health, playback addresses and unrelated library requests must keep answering while those wait. Do not reintroduce per-request owner initialization: `initialise_owner` remembers, once its transaction commits, that the household migration is done. (`fec18ed`, `d093295`)
- **Hold `lifecycle_lock` only to publish, never to hash or copy.** The exporter hashes each song before taking the lock and remembers SHA-256 per path and file identity, so an unchanged pass reads no audio; a file modified within the last 2 s is never trusted from memory. `publish()` re-reads the download under the lock and skips one replaced or in Trash since the pass began. `lifecycle_lock` and `library_lock` log any hold over 1 s with the holder's function. (`4d84405`)
- **Lock order is `covers.lock`, then `library_lock`.** See [library refresh](architecture/library-identity.md#library-refresh-and-lock-order).
- **Reads may reuse a passed mount check briefly; everything that writes, moves or deletes checks the NAS marker afresh.** (`f817343`)

## Runtime topology

Private values (addresses, host names, the public domain and edge-rule specifics) are kept out of the repository; the placeholders below stand for them.

- **Unraid host:** `<unraid-host>`, container **PigMusic**, managed by Docker Manager (template `/boot/config/plugins/dockerMan/templates-user/my-PigMusic.xml`, source `unraid/PigMusic.xml`). `scripts/deploy_release.py` recreates the container from the live template.
- **Networks:** Docker bridge `pig-music-preview` and `br0` at `<pigmusic-lan-address>:8765`. Preserve both and their gateway priorities. The release checks reach PigMusic on `br0`.
- **Owner route:** `<tailnet-origin>` through Tailscale Serve, which proxies to PigMusic's address on the `pig-music-preview` bridge. Another service shares that Tailscale Serve host; leave it unchanged.
- **Public route:** `<public-origin>` through a Cloudflare Tunnel. A `cloudflared` container (pinned tag, no published ports) sits on `pig-music-preview` with a fixed address and targets `http://PigMusic:8765` by container name, never a bridge IP, which changes on redeploy and could then expose whichever container inherits it. The edge applies the household's access, rate-limit and no-cache rules for the API.
- **Trusted proxies:** loopback, the bridge gateway used by Tailscale Serve, and cloudflared's fixed address, each listed exactly, never as a range, so login lockouts stay per visitor. `PIG_MUSIC_PUBLIC_ORIGIN` sets the origin invite links use.
- **No plain LAN port:** the host port is retired; accounts require HTTPS.
- **Storage:**
  - Appdata: `/mnt/user/appdata/pig-music-preview/data`.
  - Music: the NAS share mounted under `/mnt/remotes/` → `/library`.
  - Workspace: `/library/.pig-music-work`.
  - Recovery originals: `.pig-music-trash`.
  - Backups: `/mnt/user/appdata/pig-music-preview/backups/`.
- **Protections:** UID/GID 99:100, read-only root, dropped capabilities, no-new-privileges, two CPUs, and a 1–8 GiB memory limit (template 4 GiB; release gates test under 1 GiB). The internal engine API binds to loopback only.
- **Minimum supported native OS:** iOS/iPadOS 26; watchOS 11. The generated project settings and PigKit package agree on the iOS minimum.

Deploy and rollback procedures are in [OPERATIONS.md](OPERATIONS.md).

## Before you touch an area

Read the matching note first. Each records the rules that keep the area safe and the failures that taught them.

- **Mixes, radio and the mix cache** (`server/mixes.py`, `server/mix_cache.py`, `server/home.py`): [architecture/mixes.md](architecture/mixes.md).
- **Library identity, matching, editions, refresh and the queue** (`server/store.py`, `server/library.py`, `server/matching.py`, `server/downloader.py`, `server/organisation.py`, Tidy): [architecture/library-identity.md](architecture/library-identity.md).
- **Playback** (`server/media.py`, `server/remote.py`, PigKit playback and transfers, the iPhone player): [architecture/playback.md](architecture/playback.md).
- **Watch sync** (`swift/PigMusicWatch`, PigCompanion, the phone's Watch controller, `server/device_copies.py`): [architecture/watch-sync.md](architecture/watch-sync.md).
