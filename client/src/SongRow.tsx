import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  AudioLines,
  Cloud,
  Disc3,
  Download,
  Ellipsis,
  Heart,
  ListEnd,
  ListStart,
  UserRound,
} from "lucide-react";
import { StateBadge } from "./StateBadge";
import { api } from "./api";
import { Cover } from "./Cover";
import { PlaylistPicker } from "./PlaylistPicker";
import { usePlayer } from "./Player";
import { RemoveFromServer } from "./Recovery";
import { focusMenuItem, menuKeys } from "./menuKeys";
import { useOwner } from "./person";
import {
  goToAlbum,
  goToArtist,
  requestSong,
  songBody,
  type Song,
  type SongState,
} from "./songs";
import { InlineError } from "./EmptyState";

const duration = (n?: number) =>
  n
    ? `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, "0")}`
    : "";

/** One song. Tap anywhere to play; download state and ⋯ sit on the right. */
export function SongRow({
  song,
  queue,
  index,
  onArtist = false,
  onAlbum = false,
  numbered = false,
  menu,
  onRemove,
}: {
  song: Song;
  /** What plays after this song: usually the list it appears in. */
  queue: Song[];
  index: number;
  /** Already on this artist's or album's page: no "Go to" for it. */
  onArtist?: boolean;
  onAlbum?: boolean;
  /** Album track lists show numbers instead of repeating the cover. */
  numbered?: boolean;
  /** Extra menu items for this context, e.g. playlist ordering. */
  menu?: ReactNode;
  onRemove?: () => void;
}) {
  const player = usePlayer();
  const [state, setState] = useState<SongState>(song.state);
  const [error, setError] = useState("");
  useEffect(() => setState(song.state), [song.state, song.key]);
  const current = player.current === song.key;
  async function download() {
    setError("");
    try {
      await requestSong(song);
      setState(state === "server" ? "saved" : "pending");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const subtitle = [!onArtist && song.artist, !onAlbum && song.album]
    .filter(Boolean)
    .join(" · ");
  return (
    <div
      className={`song-row${current ? " is-current" : ""}`}
      onClick={(e) => {
        // The row is one big play target; its own buttons keep their action.
        if (!(e.target as HTMLElement).closest("button, a, [popover]"))
          player.play(queue, index);
      }}
    >
      <button
        className="song-main"
        aria-label={`Play ${song.title}${song.state === "saved" || song.cache_id ? "" : " (streams without downloading)"}`}
        onClick={() => player.play(queue, index)}
      >
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
          <strong>{song.title}</strong>
          <small>{subtitle || duration(song.duration)}</small>
        </span>
      </button>
      <span className="song-state">
        {state === "saved" ? (
          <span
            className="state-badge icon-only song-cloud"
            role="img"
            aria-label="In your library on the server"
            title="In your library on the server"
          >
            <Cloud size={20} />
          </span>
        ) : state === "pending" ? (
          <StateBadge state="requested" iconOnly />
        ) : (
          <button
            aria-label={
              state === "server" || song.cache_id
                ? `Add ${song.title} to your library`
                : `Download ${song.title} to your library`
            }
            title={
              song.cache_id
                ? "Prepared for this mix — add to your library to keep"
                : state === "server"
                  ? "On the server — add to your library"
                  : "Not downloaded — download to the server"
            }
            onClick={() => void download()}
          >
            {state === "server" ? (
              <Cloud size={20} className="song-cloud-faded" />
            ) : (
              <Download size={20} />
            )}
          </button>
        )}
      </span>
      <SongMenu
        song={song}
        state={state}
        onArtist={onArtist}
        onAlbum={onAlbum}
        download={download}
        onRemove={onRemove}
        onError={setError}
      >
        {menu}
      </SongMenu>
      {error && <InlineError className="song-error">{error}</InlineError>}
    </div>
  );
}

function SongMenu({
  song,
  state,
  onArtist,
  onAlbum,
  download,
  onRemove,
  onError,
  children,
}: {
  song: Song;
  state: SongState;
  onArtist: boolean;
  onAlbum: boolean;
  download: () => Promise<void>;
  onRemove?: () => void;
  onError: (message: string) => void;
  children?: ReactNode;
}) {
  const owner = useOwner();
  const player = usePlayer();
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [favourite, setFavourite] = useState<boolean | null>(null);
  const [position, setPosition] = useState<{
    right: number;
    top?: number;
    bottom?: number;
  }>({ right: 8, top: 8 });
  const body = songBody(song);
  function open() {
    const rect = trigger.current!.getBoundingClientRect();
    setPosition({
      right: Math.max(8, window.innerWidth - rect.right),
      ...(rect.top > window.innerHeight / 2
        ? { bottom: window.innerHeight - rect.top + 4 }
        : { top: rect.bottom + 4 }),
    });
    popup.current?.showPopover();
    focusMenuItem(popup.current, 0);
    setFavourite(null);
    void api<{ favourited: boolean }>("/v1/favourites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track: body }),
    })
      .then((d) => setFavourite(d.favourited))
      .catch(() => setFavourite(false));
  }
  const close = () => popup.current?.hidePopover();
  const item = (
    label: string,
    icon: ReactNode,
    action: () => void,
    disabled = false,
  ) => (
    <button
      role="menuitem"
      disabled={disabled}
      onClick={() => {
        close();
        action();
      }}
    >
      <span>{label}</span>
      {icon}
    </button>
  );
  return (
    <>
      <button
        ref={trigger}
        className="song-more"
        aria-label={`More options for ${song.title}`}
        aria-haspopup="menu"
        onClick={open}
      >
        <Ellipsis size={20} />
      </button>
      <div
        ref={popup}
        popover="auto"
        className="song-menu"
        role="menu"
        aria-label={song.title}
        style={position}
        onKeyDown={menuKeys}
      >
        <div className="song-menu-group">
          {item("Play next", <ListStart size={18} />, () =>
            player.playNext(song),
          )}
          {item("Add to queue", <ListEnd size={18} />, () =>
            player.addToQueue(song),
          )}
        </div>
        <div className="song-menu-group">
          {item(
            favourite ? "Remove from favourites" : "Favourite",
            <Heart size={18} fill={favourite ? "currentColor" : "none"} />,
            () => {
              onError("");
              void api<{ favourited: boolean }>("/v1/favourites", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ track: body, favourite: !favourite }),
              })
                .then(() =>
                  window.dispatchEvent(new Event("music-library-changed")),
                )
                .catch((e) => onError((e as Error).message));
            },
            favourite === null,
          )}
          <PlaylistPicker
            title={song.title}
            menuItem
            load={async () =>
              (
                await api<{ playlists: { id: string; name: string }[] }>(
                  "/v1/playlists",
                )
              ).playlists.filter((p) => !p.id.startsWith("pig-favourites:"))
            }
            create={(name) =>
              api("/v1/playlists", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name }),
              })
            }
            add={(id) =>
              api(`/v1/playlists/${id}/songs?allow_repeat=true`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
              }).then(() =>
                window.dispatchEvent(new Event("music-library-changed")),
              )
            }
          />
          {state !== "saved" &&
            state !== "pending" &&
            item(
              state === "server" || song.cache_id
                ? "Add to my library"
                : "Download to my library",
              <Download size={18} />,
              () => void download(),
            )}
        </div>
        {(!onArtist || (!onAlbum && song.album)) && (
          <div className="song-menu-group">
            {!onAlbum &&
              song.album &&
              item(
                "Go to album",
                <Disc3 size={18} />,
                () =>
                  void goToAlbum(
                    song.album!,
                    song.album_artist || song.artist,
                    song.release_id,
                    song.artist_id,
                  ),
              )}
            {!onArtist &&
              item(
                "Go to artist",
                <UserRound size={18} />,
                () => void goToArtist(song.artist, song.artist_id),
              )}
          </div>
        )}
        {(children || (onRemove && state === "saved")) && (
          <div className="song-menu-group" onClick={close}>
            {children}
            {onRemove && state === "saved" && (
              <button role="menuitem" className="danger" onClick={onRemove}>
                <span>Remove from my library</span>
              </button>
            )}
          </div>
        )}
        {owner && song.saved && (
          <div className="song-menu-group owner-menu-items">
            <span className="owner-label">Admin</span>
            <RemoveFromServer
              kind="tracks"
              id={song.saved.id}
              title={song.title}
            />
          </div>
        )}
      </div>
    </>
  );
}
