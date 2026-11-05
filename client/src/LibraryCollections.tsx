import { ListMusic } from "lucide-react";
import type { ReactNode } from "react";
import { AppleImport } from "./AppleImport";
import { CollectionPages } from "./CollectionPages";
import { Cover } from "./Cover";
import { Empty } from "./EmptyState";
import { OwnerLibraryLinks } from "./OwnerTools";
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
  createPlaylist: (name: FormDataEntryValue | null) => Promise<void>;
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
                <a
                  key={artist.id}
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
                <a
                  key={album.id}
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
          <form
            className="search-form"
            onSubmit={(e) => {
              e.preventDefault();
              const form = e.currentTarget;
              void createPlaylist(new FormData(form).get("name")).then(() =>
                form.reset(),
              );
            }}
          >
            <label>
              New playlist
              <input name="name" required maxLength={100} />
            </label>
            <button type="submit" className="secondary-button">
              Create
            </button>
          </form>
          <ul className="playlist-list">
            {playlists
              .filter((p) => !p.id.startsWith("pig-favourites:"))
              .map((p) => (
                <li key={p.id}>
                  <a href={"#/library/playlist/" + p.id}>
                    <ListMusic size={22} aria-hidden="true" />
                    <strong>{p.name}</strong>
                    <span>
                      {p.track_count ?? 0} song
                      {p.track_count === 1 ? "" : "s"}
                      {p.track_count &&
                      p.available_count !== undefined &&
                      p.available_count < p.track_count
                        ? ` · ${p.available_count} available`
                        : ""}
                    </span>
                  </a>
                </li>
              ))}
          </ul>
          {!playlists.some((p) => !p.id.startsWith("pig-favourites:")) && (
            <Empty
              title="No playlists yet"
              description="Create one above, or import your Apple Music playlists below."
            />
          )}
          {tab === "playlists" && (
            <AppleImport owner={owner} onImported={refresh} />
          )}
        </>
      )}
      {owner && <OwnerLibraryLinks />}
    </>
  );
}
