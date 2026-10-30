# Operations and recovery

Local setup and test commands are in [AGENTS.md](../AGENTS.md). Household topology and current release are in the [blueprint](../blueprint.md). Fresh appdata starts empty with downloads paused. Use Scan library against the configured music directory, then review any matching or organisation changes. `scripts/seed_prototype.py` is an optional import tool and queues nothing.

`compose.yaml` is an isolated local example. The live server is managed by Unraid Docker Manager from `unraid/PigMusic.xml`; Compose is retired there. Direct NAS mode requires the export configuration's `library_id` to match the marker on the expected library.

## Private configuration

These files live under appdata. They stay out of source control and bundles, and only the runtime owner can read them.

| File | Purpose / required fields |
| --- | --- |
| `slskd-connection.json` | Internal API `url`, `api_key`, engine `download_root` mapping |
| `engine/engine.env`, `engine/slskd.yml` | Engine login/runtime configuration; internal API stays loopback-only |
| `media-settings.json` | Portrait/remote toggles, optional `fanart_key` and `listenbrainz_token`; the API returns configured flags only |
| `library-export.json` | Publication `enabled`, expected `library_id`, relative `marker_path`, optional folder settings |

Back up and restore the whole appdata directory, including the server authentication secret. Plex sync is retired (migration 11 drops per-person tokens); delete any leftover `plex-connection.json`, which holds an owner Plex token, by hand. A copy of a live database is not a backup.

## Prepared mixes

With preloading enabled, the background mix worker prepares current and next-day editions. `PIG_MUSIC_PRELOAD=false` disables automatic preparation for isolated development/tests. Work has a four-minute per-person pass budget with durable cursors, so interrupted or slow work resumes without starting from the first song. The worker uses enabled download sources and respects the server download pause; explicit library requests take priority. Existing files provide immediate rediscovery; imported metadata can seed discoveries before files arrive. Empty libraries get clearly labelled regional charts. The adapters use ListenBrainz and the existing Apple chart; recommendations are not evidence that a source can supply audio.

Discovery files live under appdata `mix-cache/`, outside the published music library. Admin > Overview > Mix preparation shows usage/readiness, unavailable candidates, preparation issues by person and reclaimable bytes. Refreshing status preserves unsaved setting edits. Configure the budget (initially 50 GB), disk-free reserve (2 GB), preferred quality and rotation timezone (Australia/Melbourne). Those are configurable starting limits, not measured NAS capacity. Docker includes timezone data. Mix preferences, editions, feedback, leases and audio references are in SQLite migration 10.

Active/current and staged-next editions retain their files; retired/hidden editions get a cleanup grace period. Playing an edition renews a six-hour lease over its queue, including across rotation. Explicit pending library requests also retain matching cache files until completion or cancellation; successful promotion publishes an independent validated file. Cleanup cannot delete library files. Saving an edition preserves ordered metadata without permanently retaining discoveries. Explicit playback of an expired saved-edition song uses the normal downloader to refill the temporary cache and creates a six-hour playback lease; it does not require experimental streaming or create library membership. Only songs still in this person's saved mix metadata qualify. Upcoming queue entries can prepare ahead; paused/failed work remains durable. Phone offline downloads and Watch delivery keep the existing durable-library rules. Back up before adopting schema 10 or 11; restore a compatible backup when rolling back to an older schema reader.

## Container images

`.github/workflows/container.yml` builds the pinned Dockerfile, tests that exact image (backend suite, then 1 GB artwork, delivery, sync and mix scale gates), and publishes `ghcr.io/maroge1990/pig-music:sha-<rev>` (plus `:main` on main, `:vX.Y.Z` on tags). Pull requests only validate. No workflow deploys, and there is no `latest` tag. Images carry OCI source/revision labels. The package stays private.

Unraid pulls with a GitHub credential that has `read:packages`, entered through `docker login ghcr.io --password-stdin`. Never put it in templates, Git, docs, shell history or build arguments.

## Releasing

Each release has two steps:

1. **Verify the exact image** without touching the live service. After the workflow publishes the image, run:
   ```sh
   .venv/bin/python scripts/deploy_unraid.py --version <unused-name> --image ghcr.io/maroge1990/pig-music@sha256:<digest> --key <ssh-key> | tee verify.log
   ```
   It pulls the image and checks its labels against the local checkout, which must be clean and at the image's revision. It then runs the image tests and scale gates on Unraid.
2. **Release:**
   ```sh
   .venv/bin/python scripts/deploy_release.py --image <digest> --revision <sha> --version <same-name> --verified-log verify.log
   ```
   Without `--apply` it is a read-only preflight. It checks:
   - the image and its labels;
   - Docker Manager ownership, a 1–8 GiB memory limit, UID/GID 99:100, a read-only root and both fixed addresses;
   - private HTTPS, that the owner account exists, and that no transfers are active.

   With `--apply` it:
   1. pauses downloads and stops the container;
   2. backs up appdata, the template and autostart;
   3. computes the baseline from the backup with the old image;
   4. verifies migration on a disposable copy with the new image;
   5. recreates PigMusic from the template, keeping `PigMusic-before-<version>`;
   6. checks readiness, HTTPS, owner state and protections;
   7. restores the original download setting.

   Any failure restores the complete stopped appdata, template, container and networks.

`deploy_release.py --retention 2` lists backups and containers older than the two newest releases. It always keeps `pre-first-run-20261007-v1` and legacy manager material, and it **never deletes**. Remove listed items by hand once the owner agrees.

Historical tools, kept for migration history only: `deploy_unraid.py --deploy` (the old Compose path, which refuses account releases), `deploy_first_run_unraid.py`, `adopt_unraid_template.py`, `package_unraid_preview.py`, `unraid_preview_smoke.py` and `export_library_snapshot.py`. `deploy_unraid.py --deploy`, `deploy_first_run_unraid.py` and `adopt_unraid_template.py` still call the unversioned `/api/...` routes retired in U01-4, so they fail against current images; they are marked as such and must be migrated and re-reviewed before any reuse.

## Rollback and partial failures

Read the command result: a failed rollback is not recovery. A failed update leaves downloads paused until the library and state are confirmed. Restarting an old image against a migrated database is not a rollback; restore the matching pre-release appdata backup with it.

Manual recovery order:

1. Inspect container names, images, queue and storage status, without dumping private environment values.
2. Stop the candidate before reconnecting the old container to the static LAN address. Disconnect the candidate from `br0` to avoid an IP collision.
3. Restore the previous container, template and network wiring from the stopped-state backup. Start it, then verify health, the storage marker and API reads.
4. Restore the download setting only after readiness. Compare track counts, playlist membership and queue state.
5. If durable state must be restored, stop all writers first. Don't blindly restore old state after new music has been published: compare journals and files, then reconcile with guarded recovery.

Keep old manager recovery material, images, trash and backups until the owner retires them.

## Download-source acceptance

Owner diagnostics in Admin separate **metadata reachable** from **audio transfer verified**. For an authorised test recording (a JSON file with artist, title, album, duration and optional ISRCs), run:

```sh
.venv/bin/python -m scripts.verify_download_source --source youtube|soundcloud|monochrome --track track.json --output <new-empty-dir>
```

Add `--metadata-only` to skip the audio transfer. The harness uses fresh appdata and the real pipeline. Never reuse an output directory or point the harness at the household library. For each source, check:
- artist, recording, album and duration;
- the retained codec and bitrate;
- publication and restart persistence;
- authenticated playback and range requests;
- fallback order, preview rejection and cancellation.

For temporary mix preparation, add `--mix-cache`. This verifies the real transfer and complete audio validation, reopens fresh state to verify cache persistence, checks that no queue/acquired/library-membership rows were created, then reclaims the unreferenced test audio. The report includes the measured codec/bitrate, size, hash and elapsed time. `--profile mp3_320` tests the default mix quality; `--profile native_standard` tests retained source-native audio. These tests use a new isolated output directory and explicitly override library/download paths, never household appdata or the NAS.

The yt-dlp, EJS and Node runtimes are pinned in the image. Release changes to them only through CI. YouTube cookies are uploaded in Admin (Netscape format) and are never echoed back.

## Native app installation (free Apple account)

1. Open `swift/PigMusic.xcodeproj` and select scheme **PigMusic**, which embeds **PigMusicWatch**.
2. In Xcode Settings → Accounts, add the Apple Account. In Signing & Capabilities, choose the **Personal Team** with automatic signing. If you change bundle IDs, update the Watch identifier and `WKCompanionAppBundleIdentifier` together. Keep signing changes local.
3. Enable Developer Mode on the device, then build and run. The minimum OS is iOS/iPadOS 26 and watchOS 11.
4. Sign in with the server's HTTPS address, or join with an invite link or code. People and providers are managed on the web.

Personal Team profiles expire after seven days; rebuild and reinstall when they do. Use the device journeys in [testing](testing.md#native-device-acceptance).

## Troubleshooting

- **NAS unavailable:** Cached browsing and settings stay up, while the engine, workspace and downloads wait for the correct marker. Restore the real SMB mount, never an empty folder, then verify storage and the engine before resuming.
- **External changes missing:** Run Scan library.
- **Missing artwork or popularity:** Check the configured flags and provider availability. Optional failures hide enrichment and must never block browsing.
- **Slow remote listening:** Provider resolution is independent of the library; saved playback is preferred.
- **Quality mismatch:** Check the actual file format and bitrate and the effective preference. Never bypass complete-file decoding to speed up the queue.
