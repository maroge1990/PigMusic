# Design

This page describes how PigMusic looks and is laid out: which Pig family brand files it uses, the rules that keep the artwork and colours right, and where things go on each screen.

## Layout conventions

- The owner sees the member experience plus an Admin section. Admin-only options always go at the bottom of shared views.
- Native apps have no admin tools.
- Phones use an integrated player and tab dock.
- On detail pages, settings go below the music.
- Headers are compact.
- Home shows artists and albums (no song recommendations). A Home section appears only when it has something; while sections load there is at most one quiet placeholder row, never "Finding…" or "Loading…" text. The Rewind card takes the page's own look (D-120).
- Lists of music (D-120): Play and Shuffle at the top; a playlist or Favourites shows its covers, "N songs · length"; Favourites is always first among playlists; Library tabs carry counts, and a sort names what its tab holds (Name A–Z for artists, Title A–Z for albums).
- Destructive actions that can be undone act at once with an Undo (D-79): the web's notice area, the iPhone's Undo bar and the Watch's Undo row offer it for about 10 s, and the change is sent when it runs out, or at once when the page closes or the app leaves the foreground. A dialog is kept only for what cannot be undone (emptying Trash, signing out everywhere, resetting or disabling a person). On the iPhone a song request and cancelling one also act at once with Undo; a request then waits in the phone's request outbox until the server confirms it, so nothing locks while it is sent.
- Web notices (#250): every action that succeeds or fails says so in one notice area above the player, the same for members and admin, newest last and every live Undo (it never folds), the newest two errors offering Try again or warnings to check what may have been saved, with older ones folded into an "N more" button that opens them in place (the opened list scrolls within the screen and its Show fewer stays in view), and the newest routine notice always in the last place, so nothing kept is dropped and a fresh notice is never hidden, each with a close button. A success or update clears after about 5 s; one with Undo stays for the Undo time (15 s, or until the change is sent); an error ("Couldn't … Try again.") stays until closed or replaced, and may offer Try again (one that does, and a warning, is replaced only by its own next failure). What cannot be undone is asked in the web's one confirmation dialog: a title naming the thing and the action, a plain-words line, Cancel (focused; Escape also cancels) and the action, red when destructive ("Delete for good", "Sign out everywhere else", "Disable", "Reset password"). The browser's own `confirm()` is never used.
- Song rows (D-118, #248): the web draws every song with one row (`SongRow`): cover (track number and length on album pages), title with a small star for a favourite, "Artist · Album" (the artist always; no album on its own album page), then the library mark and ⋯. Up next, Rewind and songs not yet on the server are options of it. A tap plays the song and carries on through its list; in search it plays only that song.
- Menus (D-118, #249): ⋯, a right-click and a long press open the same menu, with arrow keys, Home/End, Escape and focus back on ⋯. Song menu: Play next, Add to queue; Add to library (if not yours), Favourite, Add to playlist; Download; Go to album, Go to artist; the page's own items (Move up, Remove from playlist, Hide from mixes, Remove from Up next); Remove from library, which acts at once with Undo. Albums, artists, playlists and mixes share one collection menu: Play, Shuffle, Play next; Add to library, Add to playlist, Download; a mix adds Save and Hide this edition, which live only there. A mix opens as its own page. Every item has its icon from the table below; Add to playlist always opens the same picker. On the web Download saves the song's file to this computer.
- On the iPhone (D-118): the same song row (charts too show "Artist · Album"; the queue, downloads and Apple Music or Shazam songs not on the server are options of it), the same song menu (with Send to Apple Watch after Download, and Move earlier, Move later and Remove from Up next on Up next) and the same collection menu, from a long press on a card or row and the ⋯ on its page. Send to Apple Watch shows only while an Apple Watch is paired; Download offers Original and Smaller AAC copy. Hiding a mix edition, cancelling an artist's adding and monitoring and removing from Up next act at once with Undo (Up next leaves the queue at once; Undo puts the song back); signing out of this iPhone and discarding an unsent change ask first.
- iPhone haptics: a light tap when something leaves with an Undo, success for a favourite, a queued song or a finished batch of downloads, an error when something fails. Nothing else buzzes.
- Rows drawn in a stack are separated by a hairline from the title's edge, as system lists are; no full-width rules under headers.

## Words and icons

One word and one icon for each thing on web, iPhone and Watch (D-115 to D-117); `tests/test_vocabulary.py` fails when a retired one returns. Each platform draws its own version of the shape (SF Symbols, lucide).

| Meaning | Icon (SF Symbol / lucide) | Words |
| --- | --- | --- |
| Ask the server for music | `plus` / `Plus` | Add to library; Add N songs to library |
| Saving a copy to a device | `arrow.down.circle` / `CircleArrowDown` | Download; Couldn't download |
| Send to Apple Watch | `applewatch` | Send to Apple Watch; On Apple Watch; Waiting for Apple Watch |
| Favourite | `star` / `Star` | Favourite |
| Remove from library, delete playlist | `trash` / `Trash2` | Remove from library (Undo) |
| Remove a download from a device | `minus.circle` / `CircleMinus` | Remove download |
| Cancel, close, remove from Up next | `xmark` / `X` | |
| Hide | `eye.slash` / `EyeOff` | Hide |
| After a failure / load the latest | `arrow.clockwise` / `RotateCw` | Try again / Refresh |
| Undo | `arrow.uturn.backward` / `Undo2` | Undo |
| Rewind | `clock.arrow.circlepath` / `History` | Rewind |
| Monitor artist | `bell` / `Bell` | |
| Other editions | `square.stack.3d.up` / `Layers` | |
| Find more in the catalogue | `magnifyingglass` / `Search` | |
| More actions | `ellipsis` / `Ellipsis` | |
| Sort; move in Up next | `arrow.up.arrow.down` / `ArrowUpDown` | |
| Rename | `pencil` / `Pencil` | Rename |

Row marks: in your library `checkmark.circle.fill` / `CircleCheck` ("In your library"); downloaded on this iPhone `arrow.down.circle.fill`; waiting `clock` ("Adding" until the server has the song); downloading, a progress ring; paused `pause.circle` ("Paused"; tap to resume; an Add that meets a song you paused leaves it paused and says "Paused. Resume it in Downloads."); failed `exclamationmark.circle` / `CircleAlert` (tap to try again); no mark when a song is on the server and streams. A play triangle only ever plays music.

Words: Apple Watch and iPhone (never watch or phone); songs and albums (never tracks or releases); your library; Storage limit; every error reads "Couldn't … Try again." or says what to do next.

## Brand

Pig family **1.9.0**. The canonical source is the PigBrand repository: `tokens/brand-tokens.json`, `docs/pig-family-brand-guide.md`, `docs/integration.md` and `assets/products/pig-music`. PigMusic vendors copies, so builds and CI don't need a sibling checkout. Brand questions go to that repository's guide; this section covers only how PigMusic consumes the brand.

### Files and commands

| File | Role |
| --- | --- |
| `docs/pig-family-brand-tokens.json` | Canonical tokens; the Dockerfile copies it into the image |
| `client/src/tokens.css`, `swift/PigKit/Sources/PigBrand/Tokens.swift` | Generated from the tokens; don't edit by hand. `tokens.css` also maps the kit's `--pig-*` names onto PigMusic's roles |
| `client/src/brand/pig-radiance.css`, `client/src/brand/pig-glass.css` | The kit's web stylesheets, vendored byte for byte: radiance, and glass (`.pig-glass`), figures (`.pig-amount`) and nested radii (`.pig-concentric`) |
| `docs/brand-asset-provenance.json`, `docs/native-brand-assets.json` | Source paths and SHA-256 checksums of imported artwork |

```sh
.venv/bin/python scripts/sync_native_brand.py --source /path/to/PigBrand       # refresh imports; a new version needs review
.venv/bin/python scripts/sync_native_brand.py --check                         # verify provenance (CI)
.venv/bin/python scripts/generate_brand_tokens.py --check                     # verify generated roles (CI)
```

Don't move the JSON files: the Dockerfile, `.dockerignore`, both scripts and `tests/test_brand_delivery.py` read them at these paths.

### Rules to keep

Mix covers are app content, with draft geometric motifs rather than new product marks. Their fixed palettes are generated by `scripts/generate_brand_tokens.py` from the vendored PigBrand roles (`logo.brown/brownSecondary/pink/rose/coral/innerEar/white`, `dark.canvas`, `light.raised/accent`): one background/ink pair per kind, all eleven distinct (the six newer kinds use the owner's option B, 21 Oct). The web uses generated `--mix-*` variables and Swift uses `PigMixArtwork`; no independent colour literals belong in the cover views. These palette roles match PigBrand 1.9.0 (unchanged since 1.7.0). Actions, highlights and focus continue to use appearance-aware semantic roles. In a horizontal shelf (home songs, mixes, tab lists) the focus ring sits inside the control, so the shelf edge never clips it.

- **Artwork:** Use the approved PigMusic product assets. Never redraw, recolour, change the aspect ratio or flatten transparency. Centre the pig on its optical anchor, not on its bounding box.
- **New names:** A new wordmark or product name needs approved artwork. Don't reuse PigTV lettering.
- **Type:** Interface text uses system type (Dynamic Type on Apple platforms). The wordmark font is never shipped.
- **Playback:** Keep protected surfaces and the fixed rose accent in both appearances.
- **Native presentation:** Decorative bloom disappears with Reduce Transparency and is static with Reduce Motion. Content stays on opaque surfaces. Native pickers, toggles and system bars keep platform semantics.
- **Web interaction:** Every interactive control is at least 44 px (2.75rem). Buttons and links scale to 0.97 while pressed (full-width rows light up instead; Reduce Motion dims). A sheet leaves the way it came. Letter-spacing is in em. A sideways shelf fades the edge it can still scroll towards. A page that waits for data shows the shared Loading line, never an empty state.
- **Icons:** The iOS icon is an Icon Composer document (default, dark and mono). The Watch uses the approved dark square export with the system mask.
