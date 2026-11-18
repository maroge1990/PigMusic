import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowDown,
  ArrowUp,
  AudioLines,
  CircleArrowDown,
  Disc3,
  EyeOff,
  ListEnd,
  ListPlus,
  ListStart,
  Plus,
  Star,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { StateBadge } from "./StateBadge";
import { api } from "./api";
import { Cover } from "./Cover";
import { useFavourites, isFavourite } from "./favourites";
import { MenuButton, MenuPopup, useMenu, type MenuEntry } from "./Menu";
import {
  songMenu,
  songMenuLabels,
  tapQueue,
  type SongMenuItem,
  type SongPage,
} from "./menus";
import { PlaylistPicker, type PlaylistPickerHandle } from "./PlaylistPicker";
import { usePlayer } from "./Player";
import { addToPlaylist } from "./playlists";
import { RemoveFromServer } from "./Recovery";
import { useOwner } from "./person";
import {
  downloadSongs,
  removeFromLibrary,
  setFavourite,
  songFile,
} from "./songActions";
import {
  goToAlbum,
  goToArtist,
  requestSong,
  songBody,
  withLibrary,
  type Song,
  type SongState,
} from "./songs";
import { InlineError } from "./EmptyState";
import { formatDuration } from "./format";

const icons: Record<SongMenuItem, ReactNode> = {
  "play-next": <ListStart size={18} />,
  "add-to-queue": <ListEnd size={18} />,
  "add-to-library": <Plus size={18} />,
  favourite: <Star size={18} />,
  "add-to-playlist": <ListPlus size={18} />,
  download: <CircleArrowDown size={18} />,
  "go-to-album": <Disc3 size={18} />,
  "go-to-artist": <UserRound size={18} />,
  "move-up": <ArrowUp size={18} />,
  "remove-from-playlist": <X size={18} />,
  "hide-from-mixes": <EyeOff size={18} />,
  "remove-from-up-next": <X size={18} />,
  "remove-from-library": <Trash2 size={18} />,
};

/**
 * One song row everywhere (D-118, #248). Fixed slots: cover (or the track
 * number on album pages), title with a small star for a favourite, "Artist ·
 * Album", then the length on album pages, the library mark and ⋯. A tap
 * plays the song and carries on through its list (in search, only that
 * song); right-click, a long press and ⋯ open the one song menu.
 */
export function SongRow({
  song,
  queue,
  index,
  page = "library",
  onArtist = false,
  onAlbum = false,
  numbered = false,
  playlist,
  upNext,
  onHide,
  onPlay,
  onRemoved,
  rank,
  note,
  unavailable = false,
  refresh,
}: {
  song: Song;
  /** What plays after this song: usually the list it appears in. */
  queue: Song[];
  index: number;
  /** The page the row is on; it adds the page's own menu items. */
  page?: SongPage;
  /** Already on this artist's or album's page: no "Go to" for it. */
  onArtist?: boolean;
  onAlbum?: boolean;
  /** Album pages show the track number and length instead of the cover. */
  numbered?: boolean;
  /** A playlist's own items: Move up and Remove from playlist. */
  playlist?: {
    editable: boolean;
    first: boolean;
    moveUp: () => void;
    remove: () => void;
  };
  /** Up next: move the song or take it out of the queue. */
  upNext?: { up?: () => void; down?: () => void; remove: () => void };
  /** A mix's song: Hide from mixes. */
  onHide?: () => void;
  /** A tap does this instead of playing the list (Up next skips ahead). */
  onPlay?: () => void;
  /** After Remove from library (a page may leave). */
  onRemoved?: () => void;
  /** Rewind: the song's place in the chart, and its plays. */
  rank?: number;
  note?: string;
  /** No longer on the server: shown, not playable. */
  unavailable?: boolean;
  /** Reads this song's state from the server again, edition by edition. */
  refresh?: () => void;
}) {
  const player = usePlayer();
  const favourites = useFavourites();
  const [state, setState] = useState<SongState>(song.state);
  const [error, setError] = useState("");
  useEffect(() => setState(song.state), [song.state, song.key]);
  const current = player.current === song.key;
  const favourite = isFavourite(song, favourites);
  async function download() {
    setError("");
    try {
      // The server's answer decides the mark, never the state before the tap:
      // the tick shows only once the song is confirmed in your library.
      const result = await requestSong(song);
      if (result.created > 0) setState("pending");
      else if (song.explicit_edition) {
        // The library lookup matches any edition, so for an explicit edition
        // only the server's own answer about this song counts.
        if (result.existing > 0) setState("pending");
        refresh?.();
      } else if (result.existing > 0 || result.skipped > 0) {
        // Already on its way, already yours, or skipped: ask the library.
        const [found] = await withLibrary([{ ...song, state: "missing" }]);
        if (found.state === "saved") setState("saved");
        else if (result.existing > 0) setState("pending");
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function play() {
    if (unavailable) return;
    if (onPlay) return onPlay();
    const tap = tapQueue(page, queue, index);
    player.play(tap.queue, tap.index);
  }
  const subtitle = [song.artist, !onAlbum && song.album]
    .filter(Boolean)
    .join(" · ");
  const menu = useMenu(() => lookUpFavourite());
  const picker = useRef<PlaylistPickerHandle>(null);
  const [favourited, setFavourited] = useState<boolean | null>(null);
  function lookUpFavourite() {
    setFavourited(null);
    void api<{ favourited: boolean }>("/v1/favourites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track: songBody(song) }),
    })
      .then((d) => setFavourited(d.favourited))
      .catch(() => setFavourited(false));
  }
  const owner = useOwner();
  const actions: Record<SongMenuItem, () => void> = {
    "play-next": () => player.playNext(song),
    "add-to-queue": () => player.addToQueue(song),
    "add-to-library": () => void download(),
    favourite: () => void setFavourite(song, !favourited),
    "add-to-playlist": () => picker.current?.open(menu.trigger.current!),
    download: () => downloadSongs([song]),
    "go-to-album": () =>
      void goToAlbum(
        song.album!,
        song.album_artist || song.artist,
        song.release_id,
        song.artist_id,
      ),
    "go-to-artist": () => void goToArtist(song.artist, song.artist_id),
    "move-up": () => playlist?.moveUp(),
    "remove-from-playlist": () => playlist?.remove(),
    "hide-from-mixes": () => onHide?.(),
    "remove-from-up-next": () => upNext?.remove(),
    "remove-from-library": () =>
      void removeFromLibrary([song.saved!.id], onRemoved),
  };
  const groups: MenuEntry[][] = songMenu({
    page,
    state,
    hasFile: !!songFile(song),
    album: !onAlbum && !!song.album,
    artist: !onArtist,
    editablePlaylist: playlist?.editable,
  }).map((group) =>
    group
      .filter((item) => item !== "remove-from-library" || !!song.saved)
      .map((item) => ({
        key: item,
        label:
          item === "favourite" && favourited
            ? "Remove from favourites"
            : songMenuLabels[item],
        icon:
          item === "favourite" ? (
            <Star size={18} fill={favourited ? "currentColor" : "none"} />
          ) : (
            icons[item]
          ),
        run: actions[item],
        disabled:
          (item === "favourite" && favourited === null) ||
          (item === "move-up" && playlist?.first),
        danger: item === "remove-from-library",
      })),
  );
  return (
    <div
      className={`song-row${current ? " is-current" : ""}${rank ? " is-ranked" : ""}`}
      {...menu.target}
      onClick={(e) => {
        // The row is one big play target; its own buttons keep their action.
        if (!(e.target as HTMLElement).closest("button, a, [popover], dialog"))
          play();
      }}
    >
      <button
        className="song-main"
        disabled={unavailable}
        aria-label={`Play ${song.title}${state === "saved" || song.cache_id || state === "server" ? "" : " (streams without downloading)"}`}
        onClick={play}
      >
        {rank !== undefined && (
          <span className={rank <= 3 ? "song-rank top" : "song-rank"}>
            {rank}
          </span>
        )}
        {numbered ? (
          <span className="song-number" aria-hidden="true">
            {current ? (
              <AudioLines size={16} />
            ) : (
              song.track_number || index + 1
            )}
          </span>
        ) : (
          <Cover title={song.title} src={song.cover} sizes="48px" />
        )}
        <span className="song-text">
          <strong>
            {song.title}
            {favourite && (
              <Star
                className="song-favourite"
                size={12}
                fill="currentColor"
                aria-label="Favourite"
                role="img"
              />
            )}
          </strong>
          <small>{note ? `${subtitle} · ${note}` : subtitle}</small>
        </span>
      </button>
      {numbered && (
        <span className="song-length pig-amount">
          {formatDuration(song.duration)}
        </span>
      )}
      {upNext && (
        <span className="song-tools">
          <button
            aria-label={`Move ${song.title} up`}
            disabled={!upNext.up}
            onClick={() => upNext.up?.()}
          >
            <ArrowUp size={16} />
          </button>
          <button
            aria-label={`Move ${song.title} down`}
            disabled={!upNext.down}
            onClick={() => upNext.down?.()}
          >
            <ArrowDown size={16} />
          </button>
        </span>
      )}
      <span className="song-state">
        {state === "saved" ? (
          <StateBadge state="library" iconOnly />
        ) : state === "pending" ? (
          <StateBadge state="requested" iconOnly />
        ) : (
          <button
            aria-label={`Add ${song.title} to library`}
            title="Add to library"
            onClick={() => void download()}
          >
            <Plus size={20} />
          </button>
        )}
      </span>
      <MenuButton menu={menu} label={`More options for ${song.title}`} />
      <MenuPopup menu={menu} label={song.title} groups={groups}>
        {owner && song.saved && (
          <div className="song-menu-group owner-menu-items">
            <span className="owner-label">Admin</span>
            <RemoveFromServer
              kind="tracks"
              id={song.saved.id}
              title={song.title}
              removed={() => menu.close()}
              menuItem
            />
          </div>
        )}
      </MenuPopup>
      <PlaylistPicker
        ref={picker}
        title={song.title}
        add={(id) => addToPlaylist(id, [songBody(song)])}
      />
      {error && <InlineError className="song-error">{error}</InlineError>}
    </div>
  );
}
