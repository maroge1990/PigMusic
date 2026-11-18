import { useRef, type ReactNode } from "react";
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
  type CollectionKind,
  type CollectionMenuItem,
} from "./menus";
import { notifyError } from "./notices";
import { usePlayer } from "./Player";
import { addToPlaylist } from "./playlists";
import { PlaylistPicker, type PlaylistPickerHandle } from "./PlaylistPicker";
import { shuffled } from "./shuffle";
import { addSongsToLibrary, downloadSongs } from "./songActions";
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
  buttonClass = "song-more collection-more",
}: {
  kind: CollectionKind;
  title: string;
  songs: () => Promise<Song[]>;
  mix?: { save: () => void; hide: () => void };
  /** A page's own ⋯ sits with its Play and Shuffle. */
  buttonClass?: string;
}) {
  const player = usePlayer();
  const menu = useMenu();
  const picker = useRef<PlaylistPickerHandle>(null);
  const withSongs = (act: (list: Song[]) => unknown) => () =>
    void songs()
      .then(act)
      .catch((e) => notifyError((e as Error).message));
  const actions: Record<CollectionMenuItem, () => void> = {
    play: withSongs((list) => list.length && player.play(list, 0)),
    shuffle: withSongs((list) => list.length && player.play(shuffled(list), 0)),
    "play-next": withSongs((list) => player.playNext(list)),
    "add-to-library": withSongs((list) => addSongsToLibrary(list)),
    "add-to-playlist": () => picker.current?.open(menu.trigger.current!),
    download: withSongs((list) => downloadSongs(list)),
    "save-mix": () => mix?.save(),
    "hide-edition": () => mix?.hide(),
  };
  const groups: MenuEntry[][] = collectionMenu(kind).map((group) =>
    group.map((item) => ({
      key: item,
      label: collectionMenuLabels[item],
      icon: icons[item],
      run: actions[item],
    })),
  );
  const parts = (
    <>
      <MenuButton
        menu={menu}
        label={`More options for ${title}`}
        className={buttonClass}
      />
      <MenuPopup menu={menu} label={title} groups={groups} />
      <PlaylistPicker
        ref={picker}
        title={title}
        add={async (id) => addToPlaylist(id, (await songs()).map(songBody))}
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
