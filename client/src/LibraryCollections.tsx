import type { ReactNode } from "react";
import { AppleImport } from "./AppleImport";
import { CollectionPages } from "./CollectionPages";
import { CollectionCard, albumSongs, artistSongs } from "./CollectionMenu";
import { Cover } from "./Cover";
import { Empty } from "./EmptyState";
import { OwnerLibraryLinks } from "./OwnerTools";
import { PlaylistGrid } from "./PlaylistCards";
import { ShuffleLibrary } from "./ShuffleLibrary";
import type { Playlist, PlaylistSummary } from "./libraryTypes";
import type { Album, Artist, Library, Track } from "./types";

export const libraryTabs = [
  "artists",
  "albums",
  "musicals",
  "soundtracks",
  "compilations",
  "playlists",
  "favourites",
] as const;
export type LibraryTab = (typeof libraryTabs)[number];
// Each album section shows one category; empty sections stay hidden.
const sectionCategory = {
  albums: "album",
  musicals: "musical",
  soundtracks: "soundtrack",
  compilations: "compilation",
} as const;
type Section = keyof typeof sectionCategory;
const tabLabel = (id: string) => id[0].toUpperCase() + id.slice(1);

/** The library's front page: a tab strip over artists, album sections,
 * favourites and playlists. */
export function LibraryCollections({
  library,
  tab,
  owner,
  playlists,
  favouriteList,
  songs,
  createPlaylist,
  refresh,
}: {
  library: Library;
  tab: LibraryTab;
  owner: boolean;
  playlists: PlaylistSummary[];
  favouriteList: Playlist | null;
  /** Renders a list of songs, as the detail pages do. */
  songs: (tracks: Track[], playlist?: Playlist) => ReactNode;
  createPlaylist: (name: string) => Promise<void>;
  refresh: () => Promise<void>;
}) {
  const counts: Record<string, number> = {
    musicals: library.stats.musicals,
    soundtracks: library.stats.soundtracks,
    compilations: library.stats.compilations,
  };
  const visibleTabs = libraryTabs.filter((id) => counts[id] !== 0);
  return (
    <>
      <div className="library-toolbar">
        <nav className="personal-tabs" aria-label="Library collections">
          {visibleTabs.map((id) => (
            <a
              key={id}
              href={"#/library/" + id}
              aria-current={tab === id ? "page" : undefined}
            >
              {tabLabel(id)}
            </a>
          ))}
        </nav>
        <ShuffleLibrary total={library.stats.tracks} />
      </div>
      {!library.stats.tracks && (tab === "artists" || tab === "albums") && (
        <Empty
          title="Your library is empty"
          description="Search for an artist to add music, or import your Apple Music playlists from Playlists."
        />
      )}
      {tab === "artists" && (
        <CollectionPages<Artist>
          key={tab}
          items={library.artists.filter((a) => !a.compilation_only)}
          label="artists"
        >
          {(items) => (
            <div className="personal-grid">
              {items.map((artist) => (
                <CollectionCard
                  key={artist.id}
                  kind="artist"
                  title={artist.name}
                  songs={() => artistSongs(artist.id)}
                >
                  <a
                    className="album-card"
                    href={"#/library/artist/" + artist.id}
                  >
                    <Cover title={artist.name} src={artist.cover_url} artist />
                    <h3>{artist.name}</h3>
                    <p>
                      {artist.album_count} album
                      {artist.album_count === 1 ? "" : "s"}
                    </p>
                  </a>
                </CollectionCard>
              ))}
            </div>
          )}
        </CollectionPages>
      )}
      {tab in sectionCategory && (
        <CollectionPages<Album>
          key={tab}
          items={library.albums.filter(
            (a) => a.category === sectionCategory[tab as Section],
          )}
          label={tab}
        >
          {(items) => (
            <div className="personal-grid">
              {items.map((album) => (
                <CollectionCard
                  key={album.id}
                  kind="album"
                  title={album.title}
                  songs={() => albumSongs(album.id)}
                >
                  <a
                    className="album-card"
                    href={"#/library/album/" + album.id}
                  >
                    <Cover title={album.title} src={album.cover_url} />
                    <h3>{album.title}</h3>
                    <p>
                      {tab === "albums"
                        ? `${album.artist} · ${album.track_count} song${album.track_count === 1 ? "" : "s"}`
                        : album.artist}
                    </p>
                  </a>
                </CollectionCard>
              ))}
            </div>
          )}
        </CollectionPages>
      )}
      {tab === "favourites" &&
        (favouriteList?.entries.length ? (
          songs(
            favouriteList.entries.map(
              (e) => e.saved_track || e.household_track || e,
            ),
            favouriteList,
          )
        ) : (
          <Empty
            title="No favourites yet"
            description="Choose Favourite from a song's … menu to keep it here. Favourites never download anything by themselves."
          />
        ))}
      {tab === "playlists" && (
        <>
          <PlaylistGrid
            playlists={playlists}
            favouriteList={favouriteList}
            createPlaylist={createPlaylist}
          />
          {!playlists.some((p) => !p.id.startsWith("pig-favourites:")) && (
            <Empty title="No playlists yet" />
          )}
          <AppleImport owner={owner} onImported={refresh} />
        </>
      )}
      {owner && <OwnerLibraryLinks />}
    </>
  );
}
