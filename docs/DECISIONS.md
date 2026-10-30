# PigMusic decisions

Append-only log of the owner's product and engineering decisions, oldest first.
Never rewrite an entry: supersede it with a new one that names the entry it replaces.

The rules these decisions produce, as they stand today, are in [PRODUCT.md](PRODUCT.md) and [ARCHITECTURE.md](ARCHITECTURE.md). Entries carry the reason where one was recorded.

**D-1 (Oct 2026, Phase 1)** — One app owns orchestration and publication, with slskd embedded and upstream parsers reused under `server/vendor/` (licences retained). Never recreate separate managers. Reason: PigMusic replaces the household's Lidarr, Soularr, Music Grabber and separate slskd containers with one container, one interface, shared settings and one persistent download queue.

**D-2 (Oct 2026, Phase 1)** — Identity: Compilation, live and request context take precedence. Identical copies may serve several places but are stored once.

**D-3 (Oct 2026, Phase 1)** — Quality: No conversion. System defaults with artist/album overrides in both directions; acquire and validate a new source, then retire the old file.

**D-4 (Oct 2026, Phase 1)** — Playlists: Available songs play immediately and missing ones join as they arrive. PigMusic keeps every ordered occurrence.

**D-5 (6 Oct 2026)** — Docs consolidate into the blueprint as the single roadmap.

**D-6 (6 Oct 2026)** — GHCR (`ghcr.io/maroge1990/pig-music`, private) is the release channel for the existing Unraid server.

**D-7 (7 Oct 2026)** — Accounts with personal libraries; owner bootstrap through the first-run WebUI with a console installation code.

**D-8 (7 Oct 2026)** — Docker Manager (`unraid/PigMusic.xml`) is the only management source; Compose is retired.

**D-9 (9 Oct 2026)** — Internet via Cloudflare Tunnel gated by PigMusic accounts, not Cloudflare Access. Tailscale is not removed; it remains the owner's administration route. This replaces the earlier "LAN/Tailscale only, no public exposure" rule. Reason: Cloudflare Access is a second guest list kept outside PigMusic (an email allowlist the owner must remember to update) and it would block the native iPhone app.

**D-10 (9 Oct 2026)** — Two artist pages: library = play what you have, catalogue = find what you could have.

**D-11 (9 Oct 2026)** — Members keep their own quality.

**D-12 (9 Oct 2026)** — Recommendations sit above popularity. Popular in Australia comes from Apple only. ListenBrainz is kept only for LB Radio similarity and per-artist popularity.

**D-13 (9 Oct 2026)** — A one-time owner choice is acceptable for ambiguous artists.

**D-14 (11 Oct 2026)** — All seven acquisition sources are implemented. Existing source lists and defaults are unchanged.

**D-15 (12 Oct 2026)** — Layout: integrated phone player/tab dock, settings below the music on detail pages, compact headers, and Home without song recommendations (web and native).

**D-16 (12 Oct 2026)** — Watch music lives on the Watch, prepared by the paired phone; the Watch never receives server credentials.

**D-17 (14 Oct 2026)** — Search artist results open the full artist page, which links to the library page.

**D-18 (14 Oct 2026)** — Household files play for any member without being added first; adding still creates a library membership.

**D-19 (14 Oct 2026)** — A device download that belongs to both an album and a playlist shows under both, and the file stays on the device until both are removed.

**D-20 (14 Oct 2026)** — Playlists sent to the Watch update automatically.

**D-21 (14 Oct 2026)** — Remote playback never streams lossless, even on Wi-Fi; MP3 sources come first.

**D-22 (16 Oct 2026)** — Mix scope: discovered songs in personalised mixes are downloaded in advance to a managed server cache using the download pipeline, then played from PigMusic. Once no rotating mix or active playback needs a file, it can be removed unless it has been explicitly retained in a library. This is separate from the existing experimental remote-playback policy; cache preparation does not create library memberships.

**D-23 (16 Oct 2026)** — Chosen mix playback policy: prepare discoveries before playback. Mixes blend existing household files with discovered songs fully downloaded and validated in a managed server cache; automatic mix preparation is explicitly authorised and stays separate from imports, favourites and library acquisition. Reason: cache files play from PigMusic on web and iOS, with no dependency on a third-party stream starting when Play is pressed. Detail: [architecture/mixes.md](architecture/mixes.md).

**D-24 (16 Oct 2026)** — First-release mix provider choice: retain expanded ListenBrainz with Apple's existing public chart as the regional baseline. A broader comparison against Last.fm and Apple catalog playlists can follow when credentials/developer access are available; it does not block the release. Reason: do not add providers merely to collect more results; provider outputs sit behind a shared candidate interface so the choice is reversible.

**D-25 (16 Oct 2026)** — Advance server caching, Pig-Branding artwork and one horizontal Home row are owner-selected for mixes.

**D-26 (17 Oct 2026)** — More discovery and radio: storage is plentiful, so mixes lean on discovery: five new mix kinds, a larger daily discovery pool and an endless personal radio. Continuous radio moves out of "later scope". Forgotten favourites waits until there is listening history; Back in the mix stays a library shuffle.

**D-27 (17 Oct 2026)** — Plex integration is retired. PigMusic's own web, iPhone and Watch players replace Plex playlist export and per-person Plex tokens; migration 11 drops the token table and sync state. The trash folder keeps its `.plexignore` so a Plex server indexing the same share still skips it.

**D-28 (19 Oct 2026)** — Every rotating mix has at least 20 songs, topped up from the library where it fits. Reason: a 5-song mix is a poor experience.

**D-29 (19 Oct 2026)** — Home catalogue albums are pre-cached (server warmer and the phone) so they open like library albums.

**D-30 (19 Oct 2026)** — The iPhone no longer shows sync errors when it falls back to the cached library.

**D-31 (19 Oct 2026)** — Playlists offer adding missing songs to the server from the download button, separately from device downloads.

**D-32 (21 Oct 2026)** — The plain LAN host port is removed from the Unraid template. Reason: accounts require HTTPS, so it could not sign anyone in, and nothing uses it.

**D-33 (21 Oct 2026)** — CarPlay and standalone Watch sign-in (P03) are parked. The Watch stays phone-driven; `WKRunsIndependentlyOfCompanionApp` stays as it is.

**D-34 (21 Oct 2026)** — The six newer mix kinds get their own colours (MXC option B).

**D-35 (22 Oct 2026)** — Editions: a deluxe or remastered edition is not a new album; a single or EP, a live album and a greatest hits are. Tidying keeps the fullest edition with the better copy of each song moved into it, under the album-edition tidy contract in [PRODUCT.md](PRODUCT.md#rules-pigmusic-keeps).

**D-36 (22 Oct 2026)** — TIDY is required before the next release.

**D-37 (22 Oct 2026)** — Splitting the large native controllers (SPLIT) waits for the full release acceptance pass (V01).

**D-38 (22 Oct 2026)** — The Mac app (MAC) and Shazam share/recognition (S01/S02) are deferred indefinitely.

**D-39 (24 Oct 2026)** — The owner accepts the TIDY and MXC implementations. Later operational checks do not silently revoke or broaden that acceptance, and accepted TIDY code is not permission for bulk file operations.

**D-40 (26 Oct 2026)** — Independent review stops after at most two adversarial Codex rounds per batch; after the second round only high findings block, and medium and low findings go to `docs/FINDINGS.md` for the owner to prioritise. Reason: review must not loop.

**D-41 (28 Oct 2026)** — Build the progressive remote relay (REMOTE1). Real server tests and the Mac PigKit harness are the accepted proof; no device check is required.

**D-42 (28 Oct 2026)** — Collaborative artist credits (MATCH1): a whole credit string is searched on MusicBrainz at most once, then remembered, before the credit files under its lead or album artist. Reason: skipping that search misfiled real bands such as Earth, Wind & Fire.

<!-- Append new entries below this line. -->


**D-43 (30 Oct 2026)** — Project reset, following the playbook used for PigTV: freeze new features until the roadmap is rebuilt; review and fix recent work; one documentation structure (AGENTS.md as the router, ROADMAP.md, docs/PRODUCT, ARCHITECTURE with area notes, DECISIONS, DESIGN, TESTING, OPERATIONS, evidence, archive); open work only in GitHub Issues; test tiers T1–T5 with Claude running T1–T4 and Mark T5. Reason: the blueprint had become a status diary with several overlapping work lists.

**D-44 (30 Oct 2026)** — Medium and low review findings become GitHub issues labelled `finding` instead of rows in `docs/FINDINGS.md`; security findings go to draft security advisories. Supersedes the `docs/FINDINGS.md` part of D-40; the two-round stopping rule is unchanged.

**D-45 (30 Oct 2026)** — Remove household network details (LAN addresses, Tailscale host and address, the public domain, Cloudflare rule specifics) from every tracked file except the archive; real values live only in the gitignored `.private/` folder, and scripts read them from there. Git history is not rewritten. Reason: the repository is public.

**D-46 (30 Oct 2026)** — Sign-in checks (`Auth.ready`, `Auth.authenticate`, signed-link verification) run on their own small thread pool, so slow library or NAS work on the shared pool cannot make every request wait to sign in. The resulting session-renewal edge case is recorded for later (#26), not fixed unreviewed.

**D-47 (30 Oct 2026)** — Top five after the reset, in order: release the 30 Oct server fixes (#20); Watch delivery and battery (#64, #70, #71, #65); web listening history (#66); sign-in hardening (two private security advisories); constant idle server CPU (#63, #74, #79). Mark accepted the recommended order (reliability first) over swapping in the quick screen fixes (#60–#62), which lead Next.
