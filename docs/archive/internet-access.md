# Internet access and household invites

9 October 2026. **Owner decision:** PigMusic becomes reachable from the internet at `https://music.markrogers.au` through a Cloudflare Tunnel, gated by PigMusic's own accounts. Tailscale is not removed; it remains the owner's administration route. This replaces the earlier "LAN/Tailscale only, no public exposure" rule. Cloudflare Access was considered and rejected: it adds a second guest list kept outside PigMusic (an email allowlist the owner must remember to update) and blocks the native iPhone app.

## Product model

- The app never assumes how a server is reached. A server has one public HTTPS address (`PIG_MUSIC_PUBLIC_ORIGIN`). Cloudflare Tunnel is this household's choice; another owner could use Tailscale or their own reverse proxy.
- Not everyone owns a server. An owner signs in to their own server ("Add my server" in the iPhone app). Everyone else joins an owner's server with an **invite**.
- **Invite flow:** Admin → People → Add person (or Reset password) shows a QR code and link, `https://<origin>/#/join?code=<CODE>`. The person scans it with the phone camera (opens the web app) or inside the iPhone app, chooses a password and is signed in. The code is one-use and expires in 24 hours. In the link it sits in the URL fragment, which browsers never send in requests, Referer headers or access logs, and the web app removes it from the address bar immediately. Cloudflare terminates TLS, so it does see the code in request and response bodies (creating the invite over the public address, and completing it with the new password); create invites over Tailscale if that matters. The link carries no username; activation returns it.
- There is no email in PigMusic and no external allowlist: the People list is the only guest list. Disable a person in Admin → People to remove access.

## Protections in front of the login page

Already in the app: Argon2id hashes, per-name and per-IP failure lockouts, default-deny routes, `__Host-` secure/strict cookies, CSRF header and same-origin write checks, HSTS and CSP. Behind the tunnel, Uvicorn takes the visitor address that cloudflared appends to `X-Forwarded-For` only when the immediate peer is a configured trusted proxy (`tests/test_transport.py` covers a spoofed header), so login lockouts remain per visitor.

Cloudflare (dashboard, `markrogers.au` zone), set once — never per person:

1. **Custom rule, Block:** `ip.src.country ne "AU"` for hostname `music.markrogers.au`. Remove or widen before travelling.
2. **Rate-limiting rule:** hostname `music.markrogers.au`, method `POST`, path starts with `/api/v1/auth/` or equals `/api/v1/setup` or `/api/v1/setup/owner`; about 10 requests per 10 seconds per IP; action Block.
3. **Cache rule:** bypass cache for `music.markrogers.au/api/*` so signed media URLs and personal responses are never stored at the edge.
4. Leave Bot Fight Mode off for this hostname; it challenges the native app's API calls.

## Unraid deployment (owner-authorised step)

The household tunnel is active; current verification and outstanding owner checks are tracked in the [blueprint](../blueprint.md#owner-inputs-and-work-that-can-proceed-alongside-fixes). The procedure below is for an owner-authorised setup/rebuild, not a pending instruction to redeploy. Use a backup and the usual release procedure.

1. In Cloudflare Zero Trust → Networks → Tunnels, create a tunnel named `pig-music` and copy its token. Keep the token out of Git and chat; store it only in the Unraid container template.
2. Add a `cloudflared` container (image `cloudflare/cloudflared`, pinned to a specific tag) on the existing `pig-music-preview` Docker network with a fixed address, for example `172.25.0.10`. Command: `tunnel --no-autoupdate run`; environment `TUNNEL_TOKEN=<token>`. No published ports.
3. In the tunnel, add public hostname `music.markrogers.au` → service `http://PigMusic:8765` (the PigMusic container's name, resolved by Docker on the shared network). Do not target its bridge IP: it changes on redeploy and could then expose whichever container inherits it. Confirm the name with `docker ps` first.
4. Update the PigMusic container:
   - `PIG_MUSIC_PUBLIC_ORIGIN=https://music.markrogers.au` (invite links now use this host).
   - `PIG_MUSIC_TRUSTED_PROXIES=127.0.0.1,172.25.0.1,172.25.0.10` (keep the Tailscale gateway; add cloudflared's fixed address exactly, never a range).
5. Verify from a phone on cellular with Wi-Fi off: `https://music.markrogers.au/api/health` reports `"https": true`; sign in; create an invite and complete it on the second phone; play a saved song; confirm repeated wrong passwords lock out only the device that made them (lockouts are per username and address, so a stranger guessing `mark` cannot lock the owner out elsewhere).
6. Rollback: delete the public hostname in the tunnel (or stop cloudflared) and restore the previous `PIG_MUSIC_PUBLIC_ORIGIN`/`PIG_MUSIC_TRUSTED_PROXIES`. Tailscale access is unaffected throughout.

The Tailscale address keeps working for same-origin use (each address keeps its own sign-in cookie), but invite links always use the public origin.

## Optional configurations

Scheduling for these options lives in the [blueprint](../blueprint.md#deferred-and-optional-scope).

- **Split DNS for home playback during internet outages:** resolve `music.markrogers.au` to a LAN reverse proxy inside the house (certificate via Cloudflare DNS challenge) so the same address and cookies work locally. Not needed for normal use; traffic otherwise loops through Cloudflare.
- **Cloudflare terms:** household audio streaming through a tunnel is low volume; revisit if usage grows substantially.

## Plain LAN port (retired)

The host port `192.168.1.235:8765` is removed from `unraid/PigMusic.xml` (owner, 21 Oct): accounts require HTTPS, so it could not sign anyone in, and nothing uses it. Tailscale Serve proxies to `172.25.0.2:8765`, the tunnel to `PigMusic:8765`, and `deploy_release.py` checks `192.168.1.203:8765` on `br0`; none of these is the host port. `deploy_release.py` recreates the container from the live template (`my-PigMusic.xml`), so the owner removes the port there once: Unraid › Docker › PigMusic › Edit, remove **Web UI Port**, Apply. Then confirm Tailscale and `https://music.markrogers.au/api/health` still answer.

## Security model

Accounts stop one person from browsing or editing another's library, playlists, favourites, requests or listening history. The administrator manages the server and its files, so they are trusted with all server data. This model does not protect against a compromised administrator, NAS, Mac or Unraid host.

- **Credentials:** Argon2id password hashes. Opaque session tokens stored as hashes. Secure, HttpOnly, SameSite=Strict cookies. Browser writes need a same-origin check and the Pig request header. Devices use Bearer tokens.
- **Revocation:** Revoking a session, disabling a member or removing a membership is re-checked on every media access, including signed URLs and Range requests. The last administrator can't be disabled. Password reset is available through the interactive admin CLI.
- **Never logged or exported:** passwords, bearer tokens, setup codes.
- **First-run installation code:** The one exception. Before any owner exists, startup prints a 128-bit installation code to the operator-only container console. It is never returned over HTTP, and it stops working once an owner exists. Keep startup logs private.
- **HTTP hardening:** Same-origin CSP with no inline scripts, frame denial and HSTS. JSON bodies are capped at 1 MB before parsing (Apple XML imports at 20 MB). Validation errors omit submitted values. Failed sign-ins are rate limited, and only the administrator sees security activity.
- **Dependencies:** CI audits the locked Python and npm dependencies before publishing an image.
