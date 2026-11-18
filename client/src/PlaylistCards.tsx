import { useEffect, useRef, useState, type ReactNode } from "react";
import { Play, Plus, Shuffle, Star, X } from "lucide-react";
import { api } from "./api";
import {
  CollectionCard,
  playlistSongList,
  useCollectionMenu,
} from "./CollectionMenu";
import { Cover } from "./Cover";
import { formatListSize } from "./format";
import type { Entry, Playlist, PlaylistSummary } from "./libraryTypes";
import { InlineError } from "./EmptyState";
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
function usePlaylistFacts(playlists: PlaylistSummary[]) {
  const [facts, setFacts] = useState<Record<string, PlaylistFacts>>({});
  const ids = playlists.map((p) => `${p.id}@${p.track_count ?? 0}`).join(",");
  useEffect(() => {
    const controller = new AbortController();
    for (const p of playlists)
      void api<Playlist>("/v1/playlists/" + p.id, {
        signal: controller.signal,
      })
        .then((detail) => {
          if (!controller.signal.aborted)
            setFacts((old) => ({ ...old, [p.id]: playlistFacts(detail) }));
        })
        // A card without its covers still opens its playlist.
        .catch(() => {});
    return () => controller.abort();
  }, [ids]);
  return facts;
}

/** The Playlists tab: Favourites first, then every playlist, then New playlist. */
export function PlaylistGrid({
  playlists,
  favouriteList,
  createPlaylist,
}: {
  playlists: PlaylistSummary[];
  favouriteList: Playlist | null;
  createPlaylist: (name: string) => Promise<void>;
}) {
  const own = playlists.filter((p) => !p.id.startsWith("pig-favourites:"));
  const facts = usePlaylistFacts(own);
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
  createPlaylist: (name: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
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
        <span className="cover" aria-hidden="true">
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
              void createPlaylist(name)
                .then(() => dialog.current?.close())
                .catch((e) => setError(e.message))
                .finally(() => setBusy(false));
            }}
          >
            <label>
              Playlist name
              <input name="name" autoFocus required maxLength={100} />
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
