import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Play, Plus, Shuffle, Star, X } from "lucide-react";
import { api, isUnconfirmed } from "./api";
import {
  CollectionCard,
  playlistSongList,
  useCollectionMenu,
} from "./CollectionMenu";
import { Cover } from "./Cover";
import { formatListSize } from "./format";
import { notifyError } from "./notices";
import type { Entry, Playlist, PlaylistSummary } from "./libraryTypes";
import { Empty, InlineError } from "./EmptyState";
import { SortControl, byName } from "./SortControl";
import type { Track } from "./types";
import { usePlayer } from "./Player";
import { shuffled } from "./shuffle";
import { playlistSongs } from "./songs";
import "./PlaylistCards.css";

/** The song a playlist entry plays: yours, the household's, or as saved. */
const playable = (e: Entry) => e.saved_track || e.household_track || e;

/** What a playlist card and header show: up to four covers and its length. */
export type PlaylistFacts = { covers: string[]; seconds: number };

export function playlistFacts(playlist: Playlist): PlaylistFacts {
  const covers: string[] = [];
  let seconds = 0;
  for (const entry of playlist.entries) {
    const track = playable(entry);
    seconds += track.duration || 0;
    const cover = track.cover_url || entry.cover_url;
    if (cover && !covers.includes(cover) && covers.length < 4)
      covers.push(cover);
  }
  return { covers, seconds };
}

/** "24 songs · 1 hr 32 min", from a playlist's entries. */
export const playlistSize = (playlist: Playlist) =>
  formatListSize(playlist.entries.length, playlistFacts(playlist).seconds);

/** Four covers in a square; fewer than four show the first one. */
export function PlaylistCover({
  title,
  covers,
  sizes,
}: {
  title: string;
  covers: string[];
  sizes?: string;
}) {
  if (covers.length < 4)
    return <Cover title={title} src={covers[0]} sizes={sizes} />;
  return (
    <div className="cover playlist-mosaic" aria-hidden="true">
      {covers.map((src) => (
        <Cover key={src} title={title} src={src} sizes="128px" />
      ))}
    </div>
  );
}

/** Favourites' own cover: the star. */
export function FavouritesCover() {
  return (
    <div className="cover favourites-cover" aria-hidden="true">
      <Star fill="currentColor" />
    </div>
  );
}

/** Each playlist's covers and length, read from its own page. Only the
 * playlists tab asks; the list itself carries counts only. */
function usePlaylistFacts(playlists: PlaylistSummary[], library?: string) {
  // Facts are kept with the playlist version they were read for and shown
  // only for that version: an edit moves the revision even when the count
  // stays, and a failed read leaves a plain card, not the old facts (Codex).
  // A card's covers and length come from the library songs its entries
  // match, so a library change reads them again too (#274); the facts
  // already shown stay until that read lands.
  const [stored, setStored] = useState<
    Record<string, { version: string; library?: string; facts: PlaylistFacts }>
  >({});
  const known = useRef(stored);
  known.current = stored;
  useEffect(() => {
    const controller = new AbortController();
    for (const p of playlists) {
      const version = versionOf(p);
      const kept = known.current[p.id];
      if (kept?.version === version && kept.library === library) continue;
      const forget = () =>
        setStored((old) => {
          const { [p.id]: _, ...rest } = old;
          return rest;
        });
      void api<Playlist>("/v1/playlists/" + p.id, {
        signal: controller.signal,
      })
        .then((detail) => {
          if (controller.signal.aborted) return;
          // Only an answer for the version the list names is shown; the
          // next refresh of the list reads it again.
          if (
            (p.revision !== undefined && detail.revision !== p.revision) ||
            (p.track_count !== undefined &&
              detail.entries.length !== p.track_count)
          )
            return forget();
          setStored((old) => ({
            ...old,
            [p.id]: { version, library, facts: playlistFacts(detail) },
          }));
        })
        // A card without its covers still opens its playlist; the next
        // refresh of the list reads it again.
        .catch(() => {
          if (!controller.signal.aborted) forget();
        });
    }
    return () => controller.abort();
  }, [playlists, library]);
  const facts: Record<string, PlaylistFacts> = {};
  for (const p of playlists)
    if (stored[p.id]?.version === versionOf(p))
      facts[p.id] = stored[p.id].facts;
  return facts;
}
const versionOf = (p: PlaylistSummary) =>
  `${p.revision ?? ""}:${p.track_count ?? 0}`;

/** The Playlists tab: Favourites first, then every playlist, then New playlist. */
export function PlaylistGrid({
  playlists,
  library,
  favouriteList,
  createPlaylist,
}: {
  playlists: PlaylistSummary[];
  /** The library's revision: the songs playlist entries match. */
  library?: string;
  favouriteList: Playlist | null;
  createPlaylist: (name: string, key: string) => Promise<void>;
}) {
  const own = useMemo(
    () => playlists.filter((p) => !p.id.startsWith("pig-favourites:")),
    [playlists],
  );
  const facts = usePlaylistFacts(own, library);
  const favourites = favouriteList ? playlistFacts(favouriteList) : null;
  return (
    <div className="personal-grid playlist-grid">
      <a className="album-card" href="#/library/favourites">
        <FavouritesCover />
        <h3>Favourites</h3>
        <p>
          {formatListSize(
            favouriteList?.entries.length ?? 0,
            favourites?.seconds,
          )}
        </p>
      </a>
      {own.map((p) => (
        <CollectionCard
          key={p.id}
          kind="playlist"
          title={p.name}
          songs={() => playlistSongList(p.id)}
        >
          <a className="album-card" href={"#/library/playlist/" + p.id}>
            <PlaylistCover title={p.name} covers={facts[p.id]?.covers ?? []} />
            <h3>{p.name}</h3>
            <p>{formatListSize(p.track_count ?? 0, facts[p.id]?.seconds)}</p>
          </a>
        </CollectionCard>
      ))}
      <NewPlaylistCard createPlaylist={createPlaylist} />
    </div>
  );
}

function NewPlaylistCard({
  createPlaylist,
}: {
  createPlaylist: (name: string, key: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // A name whose create the server couldn't confirm, kept for the next try.
  const [draft, setDraft] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open) dialog.current?.showModal();
  }, [open]);
  return (
    <>
      <button
        ref={button}
        type="button"
        className="album-card new-playlist-card"
        onClick={() => setOpen(true)}
      >
        <span className="cover new-playlist-cover" aria-hidden="true">
          <Plus />
        </span>
        <span className="grid-card-title">New playlist</span>
      </button>
      {open && (
        <dialog
          ref={dialog}
          className="new-playlist-dialog"
          aria-labelledby="new-playlist-title"
          onClose={() => {
            setOpen(false);
            setError("");
            button.current?.focus();
          }}
        >
          <div className="modal-heading">
            <h2 id="new-playlist-title">New playlist</h2>
            <button aria-label="Close" onClick={() => dialog.current?.close()}>
              <X size={18} />
            </button>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const name = String(
                new FormData(e.currentTarget).get("name") || "",
              ).trim();
              if (!name) return;
              setBusy(true);
              setError("");
              // This submission's own key: only its Try again reuses it
              // (#288).
              const key = crypto.randomUUID();
              /** The notice's Try again: this same attempt, sent again. */
              const unconfirmed = (e: Error): number =>
                notifyError(
                  e.message,
                  (): Promise<unknown> =>
                    createPlaylist(name, key).then(
                      () => setDraft(""),
                      (again) =>
                        isUnconfirmed(again)
                          ? unconfirmed(again)
                          : notifyError(
                              again.message,
                              undefined,
                              "new-playlist",
                            ),
                    ),
                  "new-playlist",
                );
              void createPlaylist(name, key)
                .then(() => {
                  setDraft("");
                  dialog.current?.close();
                })
                .catch((e) => {
                  if (!isUnconfirmed(e)) return setError(e.message);
                  // The playlist may have been made (#275): the dialog
                  // steps aside so the refreshed list shows whether it was,
                  // and keeps the name for New playlist.
                  setDraft(name);
                  dialog.current?.close();
                  unconfirmed(e);
                })
                .finally(() => setBusy(false));
            }}
          >
            <label>
              Playlist name
              <input
                name="name"
                autoFocus
                required
                maxLength={100}
                defaultValue={draft}
              />
            </label>
            <button type="submit" className="primary-button" disabled={busy}>
              Create playlist
            </button>
          </form>
          {error && <InlineError>{error}</InlineError>}
        </dialog>
      )}
    </>
  );
}

type FavouritesSort = "recent" | "title" | "artist";
const favouritesSorts = [
  ["recent", "Recently added"],
  ["title", "Title"],
  ["artist", "Artist"],
] as const;

/** Favourites in the order chosen: newest first, or by title or artist. */
export function sortedFavourites(
  playlist: Playlist,
  sort: FavouritesSort,
): Playlist {
  const entries = [...playlist.entries];
  const title = (e: Entry) => playable(e).title;
  const artist = (e: Entry) => playable(e).artist;
  if (sort === "recent") entries.sort((a, b) => b.position - a.position);
  else if (sort === "title") entries.sort((a, b) => byName(title(a), title(b)));
  else
    entries.sort(
      (a, b) => byName(artist(a), artist(b)) || byName(title(a), title(b)),
    );
  return { ...playlist, entries };
}

/** The Favourites tab (D-120): the star, count and length, Play, Shuffle
 * and Sort over the songs. */
export function FavouritesList({
  playlist,
  songs,
}: {
  playlist: Playlist | null;
  songs: (tracks: Track[], playlist: Playlist) => ReactNode;
}) {
  const player = usePlayer();
  const [sort, setSort] = useState<FavouritesSort>("recent");
  if (!playlist?.entries.length) return <Empty title="No favourites yet" />;
  const shown = sortedFavourites(playlist, sort);
  const queue = playlistSongs(shown.entries);
  return (
    <>
      <header className="section-title list-header">
        <FavouritesCover />
        <div className="list-header-text">
          <h2>Favourites</h2>
          <p>{playlistSize(playlist)}</p>
          <ListActions
            disabled={!queue.length}
            play={(shuffle) =>
              player.play(shuffle ? shuffled(queue) : queue, 0)
            }
          />
        </div>
        <SortControl
          value={sort}
          options={favouritesSorts}
          onChange={setSort}
        />
      </header>
      {songs(shown.entries.map(playable), shown)}
    </>
  );
}

/** A playlist page's Play, Shuffle and ⋯, over its playable songs. */
export function PlaylistActions({ playlist }: { playlist: Playlist }) {
  const player = usePlayer();
  const songs = playlistSongs(playlist.entries);
  const { parts } = useCollectionMenu({
    kind: "playlist",
    title: playlist.name,
    songs: async () => songs,
    buttonClass: "round-action",
  });
  return (
    <ListActions
      disabled={!songs.length}
      play={(shuffle) => player.play(shuffle ? shuffled(songs) : songs, 0)}
    >
      {!playlist.favourites && parts}
    </ListActions>
  );
}

/** Play and Shuffle at the top of a list of music, with any extras after. */
export function ListActions({
  play,
  disabled,
  children,
}: {
  play: (shuffle: boolean) => void;
  disabled?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className="release-actions">
      <button
        className="primary-button"
        disabled={disabled}
        onClick={() => play(false)}
      >
        <Play size={16} fill="currentColor" aria-hidden="true" /> Play
      </button>
      <button
        className="secondary-button"
        disabled={disabled}
        onClick={() => play(true)}
      >
        <Shuffle size={16} aria-hidden="true" /> Shuffle
      </button>
      {children}
    </div>
  );
}
