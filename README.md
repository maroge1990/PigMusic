# PigMusic

Private household music-library management in one Unraid container: shared settings, a durable download queue and an embedded Soulseek engine. Verified files are published into the existing Synology library that Plex reads. There are web, iPhone/iPad and Apple Watch clients.

- **Working on the code:** [AGENTS.md](AGENTS.md) covers setup, run, test and ship, and maps the docs.
- **What it does and the rules it keeps:** [docs/PRODUCT.md](docs/PRODUCT.md).
- **Current state and what's next:** [ROADMAP.md](ROADMAP.md); open work is in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues).
- **Deploy, release, recovery, internet access and security:** [docs/OPERATIONS.md](docs/OPERATIONS.md).
- **Testing, release and device checks:** [docs/TESTING.md](docs/TESTING.md).
- **Design and brand:** [docs/DESIGN.md](docs/DESIGN.md).

## Capabilities

- Daily personalised mixes on Home, with prepared discovery downloads, branded covers and saved editions on web and iOS/iPadOS 26+.
- Personal accounts and libraries over shared files, joined by one-use invites.
- Saved and catalogue browsing, artist portraits and backgrounds, discographies, popular music and recommendations.
- Song, album and artist requests, optional monitoring, parallel transfers and a recoverable queue.
- Seven acquisition sources: Soulseek, FreeMp3Cloud, zvu4no, JioSaavn, YouTube, SoundCloud and Monochrome. Each file is validated before publication, and nothing is ever transcoded to reach a quality.
- System, artist and album quality preferences, with replacement upgrades and downgrades.
- Native scanning, reviewed naming and tagging, recoverable removal and undo.
- Reviewed Apple XML imports, ordered playlists, favourites that never download.
- Native apps with offline downloads, gapless playback and crossfade, and an independent Watch library.

Private runtime data, credentials, caches and exports stay out of Git and deployment bundles. Upstream provenance and licences are under `server/vendor/`.
