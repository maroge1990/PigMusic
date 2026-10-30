# Operations and recovery

This page explains how the household PigMusic server is set up, reached from the internet, released, backed up and recovered when something goes wrong.

Local setup and test commands are in [AGENTS.md](../AGENTS.md); test tiers and device checks are in [TESTING.md](TESTING.md); the current release and planned work are in the [roadmap](../ROADMAP.md). Fresh appdata starts empty with downloads paused. Use Scan library against the configured music directory, then review any matching or organisation changes. `scripts/seed_prototype.py` is an optional import tool and queues nothing.

`compose.yaml` is an isolated local example. The live server is managed by Unraid Docker Manager from `unraid/PigMusic.xml`; Compose is retired there. Direct NAS mode requires the export configuration's `library_id` to match the marker on the expected library.

## Household topology

Household network values are kept out of this public repository. This page uses placeholders; the real values live in two gitignored files: `.private/household.env` (the values the live scripts read; the keys are listed in [`.env.example`](../.env.example)) and `.private/operations-private.md` (household specifics such as the Cloudflare rules).

| Placeholder | Meaning | Key in `.private/household.env` |
| --- | --- | --- |
| `<unraid-host>` | The Unraid server's LAN address (SSH as `root`) | `PIG_MUSIC_UNRAID_SSH`, `PIG_MUSIC_UNRAID_ADDRESS` |
| `<pigmusic-lan-address>` | PigMusic's fixed address on `br0` | `PIG_MUSIC_LAN_ADDRESS` |
| `<tailnet-origin>` | Private HTTPS origin through Tailscale Serve | `PIG_MUSIC_TAILNET_ORIGIN` |
| `<public-origin>` | Public HTTPS origin through the Cloudflare Tunnel | `PIG_MUSIC_LIVE_PUBLIC_ORIGIN` |

- **Unraid host:** `<unraid-host>`, container **PigMusic**, managed by Docker Manager (template `/boot/config/plugins/dockerMan/templates-user/my-PigMusic.xml`, source `unraid/PigMusic.xml`).
- **Networks:** bridge `pig-music-preview` at 172.25.0.2 and `br0` at `<pigmusic-lan-address>:8765`. Preserve both and their gateway priorities.
- **Private HTTPS:** `<tailnet-origin>` through Tailscale Serve. Trusted proxies: `127.0.0.1,172.25.0.1` (plus cloudflared's fixed address; see [Internet access](#internet-access)).
- **Storage:**
  - Appdata: `/mnt/user/appdata/pig-music-preview/data`.
  - Music: the NAS share mounted under `/mnt/remotes/` → `/library`.
  - Workspace: `/library/.pig-music-work`.
  - Recovery originals: `.pig-music-trash`.
  - Backups: `/mnt/user/appdata/pig-music-preview/backups/`.
- **Protections:** UID/GID 99:100, read-only root, dropped capabilities, no-new-privileges, two CPUs, and a 1–8 GiB memory limit (template 4 GiB; release gates test under 1 GiB). The internal engine API binds to loopback only.

## Internet access

PigMusic is reachable from the internet at `<public-origin>` through a Cloudflare Tunnel, gated by PigMusic's own accounts. Tailscale stays as the owner's administration route at `<tailnet-origin>`. Why a tunnel with PigMusic accounts rather than Cloudflare Access is recorded in [DECISIONS.md](DECISIONS.md).

### Product model and invites

- The app never assumes how a server is reached. A server has one public HTTPS address (`PIG_MUSIC_PUBLIC_ORIGIN`). Cloudflare Tunnel is this household's choice; another owner could use Tailscale or their own reverse proxy.
- Not everyone owns a server. An owner signs in to their own server ("Add my server" in the iPhone app). Everyone else joins an owner's server with an **invite**.
- **Invite flow:** Admin → People → Add person (or Reset password) shows a QR code and link, `https://<origin>/#/join?code=<CODE>`. The person scans it with the phone camera (opens the web app) or inside the iPhone app, chooses a password and is signed in. The code is one-use and expires in 24 hours. In the link it sits in the URL fragment, which browsers never send in requests, Referer headers or access logs, and the web app removes it from the address bar immediately. Cloudflare terminates TLS, so it does see the code in request and response bodies (creating the invite over the public address, and completing it with the new password); create invites over Tailscale if that matters. The link carries no username; activation returns it.
- There is no email in PigMusic and no external allowlist: the People list is the only guest list. Disable a person in Admin → People to remove access.

### Protections in front of the login page

Already in the app: Argon2id hashes, per-name and per-IP failure lockouts, a growing per-account sign-in delay with an owner warning on People (no hard lockout; the account's recent addresses skip it; D-49), default-deny routes, `__Host-` secure/strict cookies, CSRF header and same-origin write checks, HSTS and CSP. Behind the tunnel, Uvicorn takes the visitor address that cloudflared appends to `X-Forwarded-For` only when the immediate peer is a configured trusted proxy (`tests/test_transport.py` covers a spoofed header), so login lockouts remain per visitor.

Cloudflare (dashboard, the household zone) is set once, never per person: a country block rule (remove or widen it before travelling), a rate-limiting rule that blocks bursts of sign-in and setup `POST` requests per IP, a cache rule that bypasses the edge cache for the API so signed media URLs and personal responses are never stored there, and Bot Fight Mode left off for the hostname because it challenges the native app's API calls. The exact rules are in `.private/operations-private.md`.

### Tunnel setup on Unraid (owner-authorised step)

This procedure is for an owner-authorised setup or rebuild, not a pending instruction to redeploy. Use a backup and the usual release procedure.

1. In Cloudflare Zero Trust → Networks → Tunnels, create a tunnel named `pig-music` and copy its token. Keep the token out of Git and chat; store it only in the Unraid container template.
2. Add a `cloudflared` container (image `cloudflare/cloudflared`, pinned to a specific tag) on the existing `pig-music-preview` Docker network with a fixed address, for example `172.25.0.10`. Command: `tunnel --no-autoupdate run`; environment `TUNNEL_TOKEN=<token>`. No published ports.
3. In the tunnel, add the public hostname of `<public-origin>` → service `http://PigMusic:8765` (the PigMusic container's name, resolved by Docker on the shared network). Do not target its bridge IP: it changes on redeploy and could then expose whichever container inherits it. Confirm the name with `docker ps` first.
4. Update the PigMusic container:
   - `PIG_MUSIC_PUBLIC_ORIGIN=<public-origin>` (invite links now use this host).
   - `PIG_MUSIC_TRUSTED_PROXIES=127.0.0.1,172.25.0.1,172.25.0.10` (keep the Tailscale gateway; add cloudflared's fixed address exactly, never a range).
5. Verify from a phone on cellular with Wi-Fi off: `<public-origin>/api/health` reports `"https": true`; sign in; create an invite and complete it on the second phone; play a saved song; confirm repeated wrong passwords lock out only the device that made them (lockouts are per username and address, so a stranger guessing `mark` cannot lock the owner out elsewhere); after 10 wrong passwords in a day, People on the web shows a warning, and a device that signed in to that account in the last 30 days still signs in at once.
6. Rollback: delete the public hostname in the tunnel (or stop cloudflared) and restore the previous `PIG_MUSIC_PUBLIC_ORIGIN`/`PIG_MUSIC_TRUSTED_PROXIES`. Tailscale access is unaffected throughout.

The Tailscale address keeps working for same-origin use (each address keeps its own sign-in cookie), but invite links always use the public origin.

The plain LAN host port has been removed from `unraid/PigMusic.xml` because accounts require HTTPS; the owner removes it from the live template once (details in `.private/operations-private.md`).

### Optional configurations

Whether and when to adopt these is tracked in the [roadmap](../ROADMAP.md).

- **Split DNS for home playback during internet outages:** resolve the public hostname to a LAN reverse proxy inside the house (certificate via Cloudflare DNS challenge) so the same address and cookies work locally. Not needed for normal use; traffic otherwise loops through Cloudflare.
- **Cloudflare terms:** household audio streaming through a tunnel is low volume; revisit if usage grows substantially.

## Security model

Accounts stop one person from browsing or editing another's library, playlists, favourites, requests or listening history. The administrator manages the server and its files, so they are trusted with all server data. This model does not protect against a compromised administrator, NAS, Mac or Unraid host.

- **Credentials:** Argon2id password hashes. Opaque session tokens stored as hashes. Secure, HttpOnly, SameSite=Strict cookies. Browser writes need a same-origin check and the Pig request header. Devices use Bearer tokens.
- **Revocation:** Revoking a session, disabling a member or removing a membership is re-checked on every media access, including signed URLs and Range requests. The last administrator can't be disabled. Password reset is available through the interactive admin CLI.
- **Never logged or exported:** passwords, bearer tokens, setup codes.
- **First-run installation code:** The one exception. Before any owner exists, startup prints a 128-bit installation code to the operator-only container console. It is never returned over HTTP, and it stops working once an owner exists. Keep startup logs private.
- **HTTP hardening:** Same-origin CSP with no inline scripts, frame denial and HSTS. JSON bodies are capped at 1 MB before parsing (Apple XML imports at 20 MB). Validation errors omit submitted values. Failed sign-ins are rate limited, and only the administrator sees security activity.
- **Dependencies:** CI audits the locked Python and npm dependencies before publishing an image.

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

Who may do what:
- Commit to `main`. A push publishes images: Claude pushes a batch only after its two Codex rounds leave no high finding, and asks Mark otherwise (D-50).
- Verification and release are separately owner-authorised; file-move work needs the appropriate backup. No issue or task grants standing deploy permission.

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

Unraid's Docker › Edit › Apply can rewrite the live template: on 25 Oct it dropped the `br0` network and the `--cpus 2 --memory 4g` limits from ExtraParams, and the next update ran without them. After any Edit › Apply on PigMusic, compare the live template's ExtraParams with `unraid/PigMusic.xml` before the next release; the release preflight refuses a container without both networks or a 1–8 GiB memory limit.

`deploy_release.py --retention 2` lists backups and containers older than the two newest releases. It always keeps `pre-first-run-20261007-v1` and legacy manager material, and it **never deletes**. Remove listed items by hand once the owner agrees.

Historical tools, kept for migration history only: `deploy_unraid.py --deploy` (the old Compose path, which refuses account releases), `package_unraid_preview.py`, `unraid_preview_smoke.py` and `export_library_snapshot.py`. `deploy_unraid.py --deploy` still calls the unversioned `/api/...` routes retired in U01-4, so it fails against current images and must be migrated and re-reviewed before any reuse. `deploy_first_run_unraid.py` and `adopt_unraid_template.py` were removed on 30 Oct 2026 (they held household values and nothing used them); they remain in Git history.

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
3. Enable Developer Mode on the device, then build and run. The minimum OS is iOS/iPadOS 26 and watchOS 11; the generated project settings and PigKit package agree on the iOS minimum.
4. Sign in with the server's HTTPS address, or join with an invite link or code. People and providers are managed on the web.

Personal Team profiles expire after seven days; rebuild and reinstall when they do. Use the device journeys in [TESTING.md](TESTING.md#native-device-acceptance).

## Troubleshooting

- **NAS unavailable:** Cached browsing and settings stay up, while the engine, workspace and downloads wait for the correct marker. Restore the real SMB mount, never an empty folder, then verify storage and the engine before resuming.
- **External changes missing:** Run Scan library.
- **Missing artwork or popularity:** Check the configured flags and provider availability. Optional failures hide enrichment and must never block browsing.
- **Slow remote listening:** Provider resolution is independent of the library; saved playback is preferred.
- **Quality mismatch:** Check the actual file format and bitrate and the effective preference. Never bypass complete-file decoding to speed up the queue.
