# PigMusic product

PigMusic is the household's private music service. It keeps one shared music library on the NAS, lets each person keep their own collection over those files, finds and downloads missing music, and plays it on the web, iPhone, iPad and Apple Watch. This page says what PigMusic does and the rules it must keep; why each rule was chosen is in [DECISIONS.md](DECISIONS.md), and how it is built is in [ARCHITECTURE.md](ARCHITECTURE.md).

## What PigMusic does

PigMusic is a private household music-library manager with one container, one interface, shared settings and one persistent download queue. It replaces the household's Lidarr, Soularr, Music Grabber and separate slskd containers. The slskd engine stays embedded inside PigMusic. Plex reads the same Synology files.

- **Accounts and invites:** each person has their own account; people join by a one-use invite link or QR code created by the owner.
- **Personal libraries over shared files:** each person's library is a set of memberships over the household's files.
- **Finding and requesting music:** search the household library and the catalogue, then request an artist, album or song; PigMusic picks a source from seven acquisition sources, validates, tags, files and publishes the result.
- **Quality management:** system, artist and album quality defaults, explicit upgrades and downgrades by acquiring new source files, and periodic re-checks of lower-quality saves.
- **Imports, playlists and favourites:** reviewed Apple XML playlist imports, ordered playlists and a favourites playlist.
- **Mixes and radio:** private daily mixes per person and an endless personal radio that blend household files with discoveries prepared in a managed server cache.
- **Playback everywhere:** web, iPhone/iPad and Apple Watch players, offline device downloads, and Watch music prepared by the paired phone. Experimental remote playback plays songs the household does not have, without adding them.
- **Administration (web only):** library scan, album-edition tidying, Trash and recovery, export to the NAS, provider access, people and sessions, and diagnostics.

## Rules PigMusic keeps

- Phone use should be quick: find an artist, see what is saved and what is available, then request an artist, album or song without choosing a backend. Desktop supports investigation and troubleshooting. Keep technical options in Settings or collapsed disclosures; ask the owner before adding nonessential main-interface controls.
- A song is the same song on any edition of any album. One compatibility policy (`matching.compatible`: lead artist, edition-free title, recording kind, agreeing known recording IDs and lengths) serves library matching, queue deduplication, duplicate detection and source selection. A request (playlist import, search, mix, album) is satisfied by the household's copy wherever it sits; a missing song is downloaded from any edition's source and filed and tagged, with its cover, by known identity first: a requested release keeps its release group and moves only to that group's recommended edition holding the same recording; a requested recording is filed under the best album holding it; a request with neither goes to the artist's own studio album containing the song, on its recommended edition. Live, remix, acoustic, demo and similar recordings stay distinct (by title, album title or release type: `recording_kind`, carried from the catalogue release through presence, lookup and requests), and the original request is kept apart from the filing (`requested`). The only exception is an edition chosen explicitly from "Other versions" (`explicit_edition`): it keeps that exact release, and conflicting known release or recording IDs never become a confident match there. Uncertain or ambiguous matches require review.
- Each person has their own library memberships over shared files. One exact file may back several memberships and is stored once. Removing a membership keeps files that are still needed elsewhere.
- Quality defaults apply system → artist → album, with album the most specific. Explicit upgrades **and downgrades acquire new source files**; never transcode to reach a target quality. Validate the replacement before retiring the existing file. Fallback order:
  1. preferred quality;
  2. MP3;
  3. AAC 256 kbps (lowest rank). AAC is used only when JioSaavn is an enabled source, only for requests made after this fallback was added, and never when the owner chooses to wait for lossless.

  Lower-quality saves are re-checked every `recheck_hours`. Upgrade jobs never fall back, and automatic improvements never downgrade.
- Import is reviewed before acquisition. An Apple XML playlist becomes a persistent ordered playlist. Available files play immediately; missing files join after acquisition. Importing an inventory must not download everything.
- Favourites are a special playlist and membership only. Starring a missing song never submits a download.
- Personalised mixes blend existing household files with discoveries downloaded ahead of playback into a managed server cache. This authorises temporary mix preparation, not automatic library membership or NAS publication. Retire cached discoveries when no active mix or playback needs them; explicitly added library songs must remain available independently of cache cleanup.
- Existing music can be scanned without modification. Naming/tagging changes need a concrete review, recoverable originals and hash/path guards. Never bulk-reorganise music as a side effect of a code migration.
- Only multi-artist compilations are separated; a single artist's greatest hits stays with that artist. Soundtracks and musicals get their own sections. Empty section headings stay hidden.
- **Album-edition tidy contract:** keep the fullest edition of the same artist/album only when at least half their songs are shared; singles/EPs, live albums, greatest hits and compilations remain distinct. Explicit editions are excluded. Shared songs go to Trash only when the kept copy is demonstrably as good; a clearly better source (lossless over lossy, or 15% higher known bitrate) may replace it in the kept edition with correct tags. Unknown quality stays for review; edition-only songs stay put. Apply one reviewed album at a time with file/plan guards and durable journals. Undo settles interrupted work before reversing recorded changes; unresolved historical moves/removals require an explicit owner decision, never guessed adoption. Unpublished keepers cannot justify trashing the only published music-folder copy.
- Internet access uses one public HTTPS origin (`<public-origin>` via Cloudflare Tunnel), gated only by PigMusic accounts. Tailscale remains the owner's administration route. People join only by a one-use invite link or QR code, never an external allowlist. See [runtime topology](ARCHITECTURE.md#runtime-topology). Secrets stay server-side and never enter Git, bundles, logs, browser responses or docs.
- Fanart and ListenBrainz are optional. Without a key or token, their artist enhancements disappear quietly. Remote playback is experimental and provider-dependent; it never adds a file to the library. Household files play for every member without being added. Their listens still count in that person's listening history.
- The owner sees the member experience plus an Admin section, with admin options always at the bottom of shared views. Administration stays web-only.

## Known limits

- Experimental remote playback may fail or be slow; saved playback is independent of it.
- Provider metadata and availability are external. Optional enrichment must never block browsing or expose credentials.
- If the NAS disappears, cached browsing and settings stay available, while managed writes and transfers pause. Never create a substitute empty library or bypass the marker check.

## Set aside

These are outside current scope. Each needs an explicit owner decision and a GitHub issue before any work starts.

- **Mac app (MAC):** Designed-for-iPad app on Apple Silicon; Catalyst only if that proves unsuitable. Set aside by the owner.
- **Shazam (S01/S02):** share a song from Shazam into a private PigMusic song inbox, or recognise a song inside PigMusic with ShazamKit; never an automatic download. Set aside by the owner.
- **CarPlay and standalone Watch sign-in (P03):** the Watch remains phone-driven; do not change independent-app flags as incidental cleanup. Set aside by the owner.
- **Later mix tuning (MX0):** compare Last.fm/Apple catalogue access, improve affinity/exposure/skip signals, and consider Forgotten favourites once listening history exists.
- **Optional network setups (NET):** split DNS for home playback during internet outages; reconsider tunnel suitability if household usage grows.
