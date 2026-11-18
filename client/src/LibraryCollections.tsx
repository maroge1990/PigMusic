import { useState, type ReactNode } from "react";
import { formatCount } from "./format";
import { SortControl, byName } from "./SortControl";
import { AppleImport } from "./AppleImport";
import { CollectionPages } from "./CollectionPages";
import { CollectionCard, albumSongs, artistSongs } from "./CollectionMenu";
import { Cover } from "./Cover";
import { Empty } from "./EmptyState";
import { OwnerLibraryLinks } from "./OwnerTools";
import { FavouritesList, PlaylistGrid } from "./PlaylistCards";
import { PlayLibrary } from "./PlayLibrary";
import { LibrarySongsTab } from "./LibrarySongsTab";
import type { Playlist, PlaylistSummary } from "./libraryTypes";
import type { Album, Artist, Library, Track } from "./types";

export const libraryTabs = [
  "artists",
  "albums",
  "musicals",
  "soundtracks",
  "compilations",
  "songs",
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

// Each tab's sort reads for what it holds (D-120). The server sends artists
// by name and albums by artist, then year: those orders are the defaults.
type ArtistSort = "name" | "name-desc";
type AlbumSort = "recent" | "artist" | "title" | "year";
const artistSorts = [
  ["name", "Name A–Z"],
  ["name-desc", "Name Z–A"],
] as const;
const albumSorts = [
  ["recent", "Recently added"],
  ["artist", "Artist A–Z"],
  ["title", "Title A–Z"],
  ["year", "Year"],
] as const;
/** The chosen sorts, kept while the app stays open. */
const rememberedSort: { artists: ArtistSort; albums: AlbumSort } = {
  artists: "name",
  albums: "recent",
};
function sortArtists(items: Artist[], sort: ArtistSort) {
  const sorted = [...items].sort((a, b) => byName(a.name, b.name));
  return sort === "name-desc" ? sorted.reverse() : sorted;
}
function sortAlbums(items: Album[], sort: AlbumSort) {
  if (sort === "artist") return items;
  const sorted = [...items];
  // Newest first; albums with no recorded time go last, in the server's order.
  if (sort === "recent")
    return sorted.sort((a, b) =>
      (b.added_at || "").localeCompare(a.added_at || ""),
    );
  // Newest first; albums without a year go last.
  if (sort === "year")
    sorted.sort(
      (a, b) =>
        Number(b.year || 0) - Number(a.year || 0) || byName(a.title, b.title),
    );
  else sorted.sort((a, b) => byName(a.title, b.title));
  return sorted;
}

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
  const artists = library.artists.filter((a) => !a.compilation_only);
  const inSection = (id: Section) =>
    library.albums.filter((a) => a.category === sectionCategory[id]);
  const counts: Record<LibraryTab, number> = {
    artists: artists.length,
    albums: inSection("albums").length,
    musicals: library.stats.musicals,
    soundtracks: library.stats.soundtracks,
    compilations: library.stats.compilations,
    songs: library.stats.tracks,
    playlists: playlists.filter((p) => !p.id.startsWith("pig-favourites:"))
      .length,
    favourites: favouriteList?.entries.length ?? 0,
  };
  // Empty album sections stay hidden; the others show even when empty.
  const visibleTabs = libraryTabs.filter(
    (id) => !(id in sectionCategory) || id === "albums" || counts[id] !== 0,
  );
  const [sort, setSort] = useState(() => ({ ...rememberedSort }));
  const choose = (key: "artists" | "albums", value: string) => {
    rememberedSort[key] = value as never;
    setSort({ ...rememberedSort });
  };
  const music = tab === "artists" || tab in sectionCategory;
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
              <span className="nav-count pig-amount">
                {formatCount(counts[id])}
              </span>
            </a>
          ))}
        </nav>
        {music && (
          <div className="library-actions">
            <PlayLibrary total={library.stats.tracks} />
            {tab === "artists" ? (
              <SortControl
                value={sort.artists}
                options={artistSorts}
                onChange={(v) => choose("artists", v)}
              />
            ) : (
              <SortControl
                value={sort.albums}
                options={albumSorts}
                onChange={(v) => choose("albums", v)}
              />
            )}
          </div>
        )}
      </div>
      {!library.stats.tracks && (tab === "artists" || tab === "albums") && (
        <Empty
          title="Your library is empty"
          description="Search for an artist to add music, or import your Apple Music playlists from Playlists."
        />
      )}
      {tab === "artists" && (
        <CollectionPages<Artist>
          key={tab + sort.artists}
          items={sortArtists(artists, sort.artists)}
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
          key={tab + sort.albums}
          items={sortAlbums(inSection(tab as Section), sort.albums)}
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
      {tab === "songs" && (
        <LibrarySongsTab
          revision={library.revision || library.snapshot.captured_at}
          total={library.stats.tracks}
        />
      )}
      {tab === "favourites" && (
        <FavouritesList playlist={favouriteList} songs={songs} />
      )}
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
