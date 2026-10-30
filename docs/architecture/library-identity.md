# Library identity

PigMusic has to know when two files are "the same song", which album a song belongs to, and which artist a credit really means. If it gets this wrong, links, favourites and playlists can quietly move to the wrong album, or a song gets downloaded twice. This note records the rules that keep identity stable and the failures that taught them.

The product rule (a song is the same song on any edition) is in [PRODUCT.md](../PRODUCT.md#rules-pigmusic-keeps); the album-edition tidy contract is there too.

## Identity invariants

- Snapshot track IDs stay stable; acquired records supersede them for published replacements.
- Album edition anchors (`album-edition-anchors` document) keep the original album ID on its first known edition, so scan order cannot move links or preferences between editions. Unknown tags may join a sole known edition but never choose among several.
- Removal exclusions are edition-aware, and a restore clears only its own exclusions.
- Queue workers use claim tokens, so a cancelled or stale worker cannot publish into a reused request ID. Artist jobs use a separate generation guard.

Change none of these without migration fixtures and a backed-up production-state comparison.

Code: anchors are read in `Store._build_library` and saved in `Store._publish_library` (`server/store.py`). `Store.claim_request` gives each claimed request a fresh `claim_token`, and `Store.request_active(request_id, claim_token)` refuses a worker whose token no longer matches.

## Song and edition matching

One compatibility policy, `matching.compatible` (`server/matching.py`), serves library matching, queue joins, duplicate detection and source selection: same lead artist, edition-free title and recording kind, agreeing known recording IDs and lengths. `song_key` carries the recording kind. (`7a520fa`)

- Studio and live requests with the same title and length stay separate jobs; known, different recording IDs are different recordings.
- The resolver uses known identity and release type first: a requested release keeps its release group (recommended edition holding the same recording), a requested recording keeps that recording, and Live/Remix/Demo release types become `recording_kind` (stored in the job and the published file). The original request stays in `requested`.
- Explicit requests (`explicit_edition`, set by the server on a non-recommended release and sent back by web and iPhone) get their own queue identity and never join an ordinary job; an ordinary request may join a queued explicit one.
- Soulseek and other sources match non-explicit requests by edition-free title and lead artist, keeping distinct recordings apart, with the shared length tolerance.
- Before searching, the worker resolves a song without a release to the artist's studio album containing it on its recommended edition, retags the file to it, stores and embeds that release's cover, and completes the request from the library when another edition is already there. Album cover lookup ignores edition qualifiers and falls back to the release holding the album's songs. (`d2d1050`)
- Duplicate-song cleanup lists only PigMusic downloads of songs the library already had, never NAS files or explicit editions, and moves listeners to the kept copy. (`d2d1050`)
- A remote-playback miss is keyed by ISRCs, length, explicit edition and recording kind as well as the song name, so one request's identity never blocks another's. (`a98570d`)

## Library refresh and lock order

`Store.refresh_library` builds the library (snapshot and database reads, cover lookups, index, fingerprint) outside `library_lock`, which is held only to swap the result in, so readers never wait for a build. (`9e75f44`)

- **Refreshes run one at a time and never overwrite anchors.** Two overlapping builds could both read the edition anchors before either saved them, so the later one overwrote an anchor the first had published and an album's ID could move to another edition. Each build must read the anchors the previous one saved. (`dc669b8`)
- **The order is always `covers.lock`, then `library_lock`.** A separate refresh lock deadlocked against a scan, which holds `covers.lock` through its refresh while another refresh waited for it with `lifecycle_lock` held. `covers.lock`, held from build to swap, now serialises refreshes; readers take only `library_lock`, held just for the swap. Do not add a second refresh lock. (`672354f`)
- The exporter rebuilds the library only when it recorded a new location. (`9e75f44`)

## Artist identity from collaborative credits

Library artists come from album-artist tags; a cast or soundtrack album without one files each song under its whole credit. Searching MusicBrainz for whole credits ("A, B & C", Hamilton cast credits) left those library "artists" with no catalogue page, portrait or discography. `resolve_artist` now works in this order: (`ddfffb7`, `07e9a74`, `724ba38`)

1. The whole name is always tried first (release credits, then one exact search, its miss remembered through the stored identity), so real bands such as "Earth, Wind & Fire", "Simon & Garfunkel" or "Hall & Oates" on a compilation keep their own name.
2. Only then does a credit file under the album artist (cast or soundtrack) or its lead (release credits, else an exact lead search).
3. Several same-named leads are the owner's choice (Ambiguous), never resolved by album order.
4. MusicBrainz's catch-all artists (Various Artists, [unknown], [anonymous], [no artist], [traditional]) are excluded from every automatic path.

The identity remembers the credit it was found for, so it is reused; tags and identities already stored are unchanged.

## Album editions and Tidy

Tidy applies the album-edition contract one reviewed album at a time. Lessons that keep it safe:

- A copy goes to Trash only with evidence the kept file is as good (`as_good`): unknown quality, or lossy copies without bitrates, stay for review. Before, a FLAC on another edition was trashed when the kept copy's quality was unknown. (`2d925c8`)
- Every move and removal ID is journaled before the step runs, and memberships are written in the same transaction as the journal. Undo first settles an interrupted step, then reverses it. (`2d925c8`)
- A moved file takes exactly the kept album's placement: `Downloader.tag(clear=)` removes absent fields (release ID, year) instead of keeping the donor's. (`2d925c8`)
- Apply validates the kept copy behind every removal, not only the files that move; a kept file changed or missing since the scan refuses the album before anything moves. (`a442739`)
- An unrecorded removal is never adopted on a guess; an unrecorded move is adopted only by the attempt whose last recorded removal is the copy it replaced. Anything unproven is listed as unresolved for the owner to include in Undo or set aside, and Undo stays "stopped" while any remain. A removal that never reached Trash (original in place and hash-verified, no Trash copy) is cancelled without repeating the filesystem operation. (`144a294`)
