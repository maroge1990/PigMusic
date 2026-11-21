import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  CircleArrowDown,
  EyeOff,
  ListPlus,
  ListStart,
  Play,
  Plus,
  Shuffle,
} from "lucide-react";
import { api } from "./api";
import type { Playlist } from "./libraryTypes";
import { MenuButton, MenuPopup, useMenu, type MenuEntry } from "./Menu";
import {
  collectionMenu,
  collectionMenuLabels,
  isLatest,
  latestChoice,
  type ActionSlot,
  type Choice,
  type CollectionKind,
  type CollectionMenuItem,
} from "./menus";
import { notifyError } from "./notices";
import { chooseReplace } from "./playChoice";
import { usePlayer } from "./Player";
import { AttemptEnded, addToPlaylist } from "./playlists";
import { PlaylistPicker, type PlaylistPickerHandle } from "./PlaylistPicker";
import { shuffled } from "./shuffle";
import {
  addSongsToLibrary,
  downloadSongs,
  withCollection,
} from "./songActions";
import { fromTrack, playlistSongs, songBody, type Song } from "./songs";
import type { Track } from "./types";

const icons: Record<CollectionMenuItem, ReactNode> = {
  play: <Play size={18} />,
  shuffle: <Shuffle size={18} />,
  "play-next": <ListStart size={18} />,
  "add-to-library": <Plus size={18} />,
  "add-to-playlist": <ListPlus size={18} />,
  download: <CircleArrowDown size={18} />,
  "save-mix": <Plus size={18} />,
  "hide-edition": <EyeOff size={18} />,
};

/** A library album's songs, in album order. */
export async function albumSongs(id: string): Promise<Song[]> {
  const album = await api<{ tracks: Track[]; cover_url?: string | null }>(
    "/v1/albums/" + id,
  );
  return album.tracks
    .filter((t) => t.available !== false)
    .map((t) => fromTrack(t, album.cover_url));
}

/** A library artist's songs: album by album, then compilations. */
export async function artistSongs(id: string): Promise<Song[]> {
  const artist = await api<{
    tracks?: Track[];
    compilation_tracks?: Track[];
  }>("/v1/artists/" + id);
  return [...(artist.tracks || []), ...(artist.compilation_tracks || [])]
    .filter((t) => t.available !== false)
    .map((t) => fromTrack(t));
}

export async function playlistSongList(id: string): Promise<Song[]> {
  const playlist = await api<Playlist>("/v1/playlists/" + id);
  return playlistSongs(playlist.entries);
}

/**
 * The one menu for an album, artist, playlist or mix (D-118, #249): Play,
 * Shuffle, Play next; Add to library, Add to playlist, Download; a mix adds
 * Save and Hide this edition. Its songs are read when an item needs them.
 */
export function useCollectionMenu({
  kind,
  title,
  songs,
  mix,
  addToLibrary = addSongsToLibrary,
  cannotAdd = false,
  canAddToLibrary = true,
  buttonClass = "song-more collection-more",
}: {
  kind: CollectionKind;
  title: string;
  songs: () => Promise<Song[]>;
  mix?: { save: () => void; hide: () => void };
  /** Add to library for this collection; `tryAgain` repeats the choice. */
  addToLibrary?: (
    list: Song[],
    session: string | null,
    tryAgain: () => void,
  ) => unknown;
  /** Known to have nothing left to add: Add to library is off, as the
   * album page's Add is. */
  cannotAdd?: boolean;
  /** False where the collection has no Add to library at all. */
  canAddToLibrary?: boolean;
  /** A page's own ⋯ sits with its Play and Shuffle. */
  buttonClass?: string;
}) {
  const player = usePlayer();
  const menu = useMenu();
  const picker = useRef<PlaylistPickerHandle>(null);
  // While the songs are read (a catalogue album is looked up first), the ⋯
  // shows the spinner. Each kind of action keeps only its latest choice: a
  // second click, or Shuffle after Play, replaces the first, so nothing
  // acts twice; nothing acts once the card has gone (#267).
  const latest = useRef(new Map<ActionSlot, Choice>());
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [busy, setBusy] = useState(0);
  // Every action takes its session before reading the songs (Codex).
  const withSongs =
    (
      slot: ActionSlot,
      act: (list: Song[], session: string | null, again: () => void) => unknown,
    ) =>
    () => {
      const choice = latestChoice(latest.current, slot);
      if (!choice) return;
      // Play or Shuffle also gives way to any replacing choice made since,
      // from any card, row, page or button (playChoice.ts).
      const newest = slot === "play" ? chooseReplace() : () => true;
      const current = () =>
        alive.current && isLatest(latest.current, choice) && newest();
      const again = withSongs(slot, act);
      setBusy((n) => n + 1);
      void withCollection(songs, (list, session) =>
        current() ? act(list, session, again) : undefined,
      )
        .catch((e) => {
          if (current()) notifyError((e as Error).message);
        })
        .finally(() => {
          choice.done = true;
          if (alive.current) setBusy((n) => n - 1);
        });
    };
  const actions: Record<CollectionMenuItem, () => void> = {
    play: withSongs("play", (list) => list.length && player.play(list, 0)),
    shuffle: withSongs(
      "play",
      (list) => list.length && player.play(shuffled(list), 0),
    ),
    // Play next only adds: it neither cancels a pending Play nor is
    // cancelled by one (playChoice.ts).
    "play-next": withSongs("queue", (list) => player.playNext(list)),
    "add-to-library": withSongs("add", (list, session, again) =>
      addToLibrary(list, session, again),
    ),
    "add-to-playlist": () => picker.current?.open(menu.trigger.current!),
    download: withSongs("download", (list) => downloadSongs(list)),
    "save-mix": () => mix?.save(),
    "hide-edition": () => mix?.hide(),
  };
  const groups: MenuEntry[][] = collectionMenu(kind, canAddToLibrary).map(
    (group) =>
      group.map((item) => ({
        key: item,
        label: collectionMenuLabels[item],
        icon: icons[item],
        run: actions[item],
        disabled: item === "add-to-library" && cannotAdd,
      })),
  );
  const parts = (
    <>
      <MenuButton
        menu={menu}
        label={`More options for ${title}`}
        className={buttonClass}
        busy={busy > 0}
      />
      <MenuPopup menu={menu} label={title} groups={groups} />
      <PlaylistPicker
        ref={picker}
        title={title}
        add={async (id) => {
          // Once a playlist is picked, the add the person asked for goes
          // ahead even if they leave the card (and its notice is true): the
          // card can only be gone before the pick, when the picker goes
          // with it. Only the session ending or changing stops it, and a
          // stopped add says so, so the picker never reports it added
          // (Codex rounds 1 and 2 on #267, by decision).
          let sent = false;
          await withCollection(songs, (list, session) => {
            sent = true;
            return addToPlaylist(id, list.map(songBody), session);
          });
          if (!sent) throw new AttemptEnded();
        }}
      />
    </>
  );
  return { menu, parts };
}

/** A card (album, artist, playlist or mix) with its collection menu: ⋯ over
 * the cover's corner, a right-click or a long press. */
export function CollectionCard({
  kind,
  title,
  songs,
  mix,
  className = "",
  children,
}: {
  kind: CollectionKind;
  title: string;
  songs: () => Promise<Song[]>;
  mix?: { save: () => void; hide: () => void };
  className?: string;
  children: ReactNode;
}) {
  const { menu, parts } = useCollectionMenu({ kind, title, songs, mix });
  return (
    <div className={`collection-card ${className}`.trim()} {...menu.target}>
      {children}
      {parts}
    </div>
  );
}
