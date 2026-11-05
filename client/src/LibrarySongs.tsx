import { CollectionPages } from "./CollectionPages";
import type { Entry, Playlist } from "./libraryTypes";
import { SongRow } from "./SongRow";
import { fromTrack, playlistSongs, type Song } from "./songs";
import type { Track } from "./types";

/** A library page's songs, paged; a playlist's rows can move up or leave it. */
export function LibrarySongs({
  tracks,
  playlist,
  cover,
  albumPage,
  artistName,
  remove,
  moveUp,
  removeFromPlaylist,
}: {
  tracks: Track[];
  playlist?: Playlist;
  cover?: string | null;
  /** On an album page, rows are numbered and don't repeat the album. */
  albumPage: boolean;
  /** On an artist page, rows by that artist don't repeat the name. */
  artistName?: string;
  /** Asks to remove one song from the library. */
  remove: (trackId: string) => void;
  moveUp: (playlist: Playlist, entry: Entry) => void;
  removeFromPlaylist: (playlist: Playlist, entry: Entry) => void;
}) {
  const albumArtist = albumPage && tracks[0]?.album_artist;
  const list: Song[] = playlist
    ? playlistSongs(playlist.entries, cover)
    : tracks.map((t) => fromTrack(t, cover));
  return (
    <CollectionPages items={list} label="Songs">
      {(items) => (
        <div className="song-list">
          {items.map((song) => {
            const i = list.indexOf(song);
            const t = tracks[i];
            const entry = playlist?.entries[i];
            return (
              <SongRow
                key={song.key}
                song={song}
                queue={list}
                index={i}
                numbered={!!albumArtist}
                onAlbum={!!albumArtist}
                onArtist={
                  !!(albumArtist || artistName) &&
                  t.artist.toLocaleLowerCase() ===
                    String(albumArtist || artistName).toLocaleLowerCase()
                }
                onRemove={
                  playlist || song.state !== "saved"
                    ? undefined
                    : () => remove(t.id)
                }
                menu={
                  playlist &&
                  !playlist.favourites &&
                  entry && (
                    <>
                      <button
                        role="menuitem"
                        disabled={entry.position === 0}
                        onClick={() => moveUp(playlist, entry)}
                      >
                        <span>Move up</span>
                      </button>
                      <button
                        role="menuitem"
                        className="danger"
                        onClick={() => removeFromPlaylist(playlist, entry)}
                      >
                        <span>Remove from playlist</span>
                      </button>
                    </>
                  )
                }
              />
            );
          })}
        </div>
      )}
    </CollectionPages>
  );
}
