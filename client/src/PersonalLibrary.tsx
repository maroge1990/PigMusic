import { AppleImport } from "./AppleImport";
import { Empty } from "./EmptyState";
import { FindMore } from "./FindMore";
import { ArtistHero } from "./ArtistHero";
import { CatalogueBrowser } from "./CatalogueBrowser";
import { Shuffle } from "lucide-react";
import { shuffled } from "./shuffle";
import { useEffect, useRef, useState } from "react";
import { Play, ArrowLeft, ListMusic, Download } from "lucide-react";
import { api } from "./api";
import { formatCount } from "./format";
import { PageHeader } from "./PageHeader";
import { PersonalQuality } from "./PersonalQuality";
import { SearchPage } from "./SearchPage";
import { Cover } from "./Cover";
import { CollectionPages } from "./CollectionPages";
import { ShuffleLibrary } from "./ShuffleLibrary";
import { SongRow } from "./SongRow";
import { fromTrack, playlistSongs, songBody, type Song } from "./songs";
import { albumSummary, mergeAlbumTracks } from "./albumTracks";
import { warmCovers, whenIdle } from "./prefetch";
import { usePlayer } from "./Player";
import type { Album, Artist, Library, Track } from "./types";
import type { Person } from "./AppShell";
import { go, type Route } from "./routes";
import {
  OwnerAlbumTools,
  OwnerArtistTools,
  OwnerLibraryLinks,
} from "./OwnerTools";

type Entry = Track & {
  entry_id: string;
  position: number;
  saved_track?: Track | null;
  household_available?: boolean;
  household_track?: Track | null;
};
type Playlist = {
  id: string;
  name: string;
  revision: number;
  favourites?: boolean;
  entries: Entry[];
};
type PlaylistSummary = {
  id: string;
  name: string;
  track_count?: number;
  available_count?: number;
};
type Preview = {
  confirmation: string;
  delete_count: number;
  keep_count: number;
  items: { track_id: string; delete_file: boolean; kept_for: string[] }[];
};
const libraryTabs = [
  "artists",
  "albums",
  "musicals",
  "soundtracks",
  "compilations",
  "playlists",
  "favourites",
] as const;
// Each album section shows one category; empty sections stay hidden.
const sectionCategory = {
  albums: "album",
  musicals: "musical",
  soundtracks: "soundtrack",
  compilations: "compilation",
} as const;
type Section = keyof typeof sectionCategory;
type LibraryTab = (typeof libraryTabs)[number];
const tabLabel = (id: string) => id[0].toUpperCase() + id.slice(1);

function albumMeta(info: {
  artist?: string;
  artistId?: string;
  year?: number;
  trackCount?: number;
}) {
  const rest = [
    info.year ? String(info.year) : "",
    info.trackCount
      ? `${info.trackCount} ${info.trackCount === 1 ? "song" : "songs"}`
      : "",
  ].filter(Boolean);
  return (
    <>
      {info.artist &&
        (info.artistId ? (
          <a href={"#/library/artist/" + info.artistId}>{info.artist}</a>
        ) : (
          info.artist
        ))}
      {rest.map((part, i) => (
        <span key={part}>
          {i > 0 || info.artist ? " · " : ""}
          {part}
        </span>
      ))}
    </>
  );
}

export function PersonalLibrary({
  user,
  route,
}: {
  user: Person;
  route: Route;
}) {
  const owner = user.role === "owner";
  const searching = route.section === "search";
  const [catalogue, setCatalogue] = useState<{
    id: string;
    name: string;
    release?: string;
    group?: string;
  } | null>(null);
  const [library, setLibrary] = useState<Library | null>(null);
  const [tab, setTab] = useState<LibraryTab>("artists");
  const [detail, setDetail] = useState<{
    /** The route this page belongs to ("album:<id>"); see openRoute. */
    route: string;
    title: string;
    artist?: Artist;
    cover?: string | null;
    quality?: { scope: "artist" | "album"; itemId: string };
    tracks?: Track[];
    albums?: Album[];
    playlist?: Playlist;
    releaseId?: string;
    albumInfo?: {
      artist?: string;
      artistId?: string;
      year?: number;
      trackCount?: number;
    };
  } | null>(null);
  // The full tracklist of the open album's release, when it could be read.
  const [release, setRelease] = useState<{
    id: string;
    tracks: Track[];
  } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const openId = useRef("");
  /** The detail page the route shows; a late response for another page is dropped. */
  const openRoute = useRef("");
  const [playlists, setPlaylists] = useState<PlaylistSummary[]>([]);
  const [favouriteList, setFavouriteList] = useState<Playlist | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [removal, setRemoval] = useState<{
    preview: Preview;
    ids: string[];
  } | null>(null);
  const [undo, setUndo] = useState<{ id: string; undo_until: number } | null>(
    null,
  );
  const dialog = useRef<HTMLDialogElement>(null);
  const player = usePlayer();
  /** Only the latest refresh may apply its answers (#97). */
  const refreshes = useRef(0);
  async function refresh() {
    const run = ++refreshes.current;
    const [summary, list] = await Promise.all([
      api<Library>("/v1/library"),
      api<{ playlists: PlaylistSummary[] }>("/v1/playlists"),
    ]);
    if (run !== refreshes.current) return;
    setLibrary(summary);
    setPlaylists(list.playlists);
    // Library artwork is always ready, whichever tab opens next.
    whenIdle(() =>
      warmCovers([
        ...summary.artists
          .filter((a) => !a.compilation_only)
          .map((a) => a.cover_url),
        ...summary.albums.map((a) => a.cover_url),
      ]),
    );
    const favouriteList = list.playlists.find((p) =>
      p.id.startsWith("pig-favourites:"),
    );
    const favourites = favouriteList
      ? await api<Playlist>("/v1/playlists/" + favouriteList.id)
      : null;
    if (run === refreshes.current) setFavouriteList(favourites);
  }
  useEffect(() => {
    void refresh().catch((e) => setError(e.message));
    const changed = () => void refresh().catch((e) => setError(e.message));
    window.addEventListener("music-library-changed", changed);
    return () => window.removeEventListener("music-library-changed", changed);
  }, []);
  const [kind, id] = route.parts;
  const routeKey = `${route.section}/${route.parts.join("/")}?${route.params}`;
  useEffect(() => {
    openRoute.current = !searching && id ? `${kind}:${id}` : "";
    // An error belongs to the page it happened on (#97).
    setError("");
    // A catalogue artist page: from Search, or from a library artist page
    // (then it stays under Library).
    if (id && ((searching && kind === "artist") || kind === "discography")) {
      setCatalogue({
        id,
        name: route.params.get("name") || "Artist",
        release: route.params.get("release") || undefined,
        group: route.params.get("group") || undefined,
      });
      return;
    }
    setCatalogue(null);
    if (searching) return setDetail(null);
    if (libraryTabs.includes(kind as LibraryTab)) setTab(kind as LibraryTab);
    openId.current = kind === "album" && id ? id : "";
    if (kind === "album" && id) void openAlbum({ id } as Album);
    else if (kind === "artist" && id) void openArtist({ id } as Artist);
    else if (kind === "playlist" && id) void openPlaylist(id);
    else setDetail(null);
  }, [routeKey]);
  useEffect(() => {
    // A finished or new download updates the open album's rows.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const changed = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (openId.current) void openAlbum({ id: openId.current } as Album);
      }, 400);
    };
    window.addEventListener("music-library-changed", changed);
    window.addEventListener("music-requests-changed", changed);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("music-library-changed", changed);
      window.removeEventListener("music-requests-changed", changed);
    };
  }, []);
  const backToLibrary = () => go("/library/" + tab);
  useEffect(() => {
    // Keep the chosen collection visible in the sideways tab strip.
    document
      .querySelector(".personal-tabs [aria-current]")
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [tab, !!library]);
  useEffect(() => {
    if (removal) dialog.current?.showModal();
  }, [removal]);
  useEffect(() => {
    if (!undo) return;
    const timer = setTimeout(
      () => setUndo(null),
      Math.max(0, undo.undo_until * 1000 - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [undo]);
  async function action(fn: () => Promise<unknown>, message = "Saved") {
    setError("");
    try {
      await fn();
      setNotice(message);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function openAlbum(album: Album) {
    try {
      const data = await api<
        Album & { tracks: Track[]; musicbrainz_release_id?: string | null }
      >("/v1/albums/" + album.id);
      if (openId.current !== album.id) return;
      const releaseId =
        data.musicbrainz_release_id ||
        data.tracks.find((t) => t.musicbrainz_release_id)
          ?.musicbrainz_release_id;
      setDetail({
        route: "album:" + album.id,
        title: data.title,
        cover: data.cover_url,
        tracks: data.tracks,
        quality: { scope: "album", itemId: data.id },
        releaseId,
        albumInfo: {
          artist: data.artist,
          artistId: data.artist_id,
          year: data.year,
          trackCount: data.track_count ?? data.tracks.length,
        },
      });
      // The rest of the tracklist arrives after the page is already usable.
      if (releaseId) void loadRelease(album.id, releaseId);
      else setRelease(null);
    } catch (e) {
      if (openId.current === album.id) setError((e as Error).message);
    }
  }
  async function loadRelease(albumId: string, releaseId: string) {
    try {
      const data = await api<{ id: string; tracks: Track[] }>(
        "/v1/catalogue/releases/" + releaseId,
      );
      if (openId.current === albumId)
        setRelease({ id: releaseId, tracks: data.tracks });
    } catch {
      // Without the release, the album simply shows what you own.
    }
  }
  async function downloadMissing(rows: Track[]) {
    const wanted = rows
      .map((t) => fromTrack(t, detail?.cover))
      .filter((s) => s.state === "missing" || s.state === "server");
    if (!wanted.length) return;
    setDownloading(true);
    setError("");
    try {
      await api("/v1/requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tracks: wanted.map(songBody) }),
      });
      const ids = new Set(wanted.map((s) => s.key));
      setRelease(
        (old) =>
          old && {
            ...old,
            tracks: old.tracks.map((t) =>
              ids.has(t.id)
                ? {
                    ...t,
                    server_state:
                      t.server_state === "household_available"
                        ? "in_library"
                        : "requested",
                  }
                : t,
            ),
          },
      );
      window.dispatchEvent(new Event("music-requests-changed"));
      window.dispatchEvent(new Event("music-library-changed"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDownloading(false);
    }
  }
  async function openArtist(artist: Artist) {
    try {
      const data = await api<
        Artist & {
          albums: Album[];
          tracks?: Track[];
          compilation_tracks?: Track[];
        }
      >("/v1/artists/" + artist.id);
      if (openRoute.current !== "artist:" + artist.id) return;
      // Newest first, like the catalogue page.
      const albums = [...data.albums].sort(
        (a, b) => Number(b.year || 0) - Number(a.year || 0),
      );
      const order = new Map(albums.map((a, i) => [a.id, i]));
      setDetail({
        route: "artist:" + artist.id,
        title: data.name,
        artist: data,
        quality: { scope: "artist", itemId: data.id },
        albums,
        // Owned songs album by album, then appearances on compilations.
        tracks: [
          ...(data.tracks || []).sort(
            (a, b) =>
              (order.get(a.album_id || "") ?? 0) -
              (order.get(b.album_id || "") ?? 0),
          ),
          ...(data.compilation_tracks || []),
        ],
      });
    } catch (e) {
      if (openRoute.current === "artist:" + artist.id)
        setError((e as Error).message);
    }
  }
  async function openPlaylist(id: string) {
    try {
      const data = await api<Playlist>("/v1/playlists/" + id);
      if (openRoute.current !== "playlist:" + id) return;
      setDetail({
        route: "playlist:" + id,
        title: data.name,
        tracks: data.entries.map(
          (t) => t.saved_track || t.household_track || t,
        ),
        playlist: data,
      });
    } catch (e) {
      if (openRoute.current === "playlist:" + id)
        setError((e as Error).message);
    }
  }
  async function preview(ids: string[]) {
    try {
      const data = await api<Preview>("/v1/library/removal-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_ids: ids }),
      });
      setRemoval({ preview: data, ids });
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function songs(tracks: Track[], playlist?: Playlist) {
    const albumArtist =
      detail?.quality?.scope === "album" && tracks[0]?.album_artist;
    const artistName = detail?.artist?.name;
    const list: Song[] = playlist
      ? playlistSongs(playlist.entries, detail?.cover)
      : tracks.map((t) => fromTrack(t, detail?.cover));
    return (
      <CollectionPages key={detail?.title || tab} items={list} label="Songs">
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
                      : () => void preview([t.id])
                  }
                  menu={
                    playlist &&
                    !playlist.favourites &&
                    entry && (
                      <>
                        <button
                          role="menuitem"
                          disabled={entry.position === 0}
                          onClick={() =>
                            void action(async () => {
                              await api(`/v1/playlists/${playlist.id}`, {
                                method: "PATCH",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({
                                  revision: playlist.revision,
                                  order: playlist.entries
                                    .map((e) => e.position)
                                    .map((p) =>
                                      p === entry.position
                                        ? p - 1
                                        : p === entry.position - 1
                                          ? p + 1
                                          : p,
                                    ),
                                }),
                              });
                              await openPlaylist(playlist.id);
                            })
                          }
                        >
                          <span>Move up</span>
                        </button>
                        <button
                          role="menuitem"
                          className="danger"
                          onClick={() =>
                            void action(async () => {
                              await api(
                                `/v1/playlists/${playlist.id}/songs/${entry.position}?revision=${playlist.revision}`,
                                { method: "DELETE" },
                              );
                              await openPlaylist(playlist.id);
                            })
                          }
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
  if (catalogue)
    return (
      <main className="personal-main">
        <CatalogueBrowser
          key={catalogue.id + (catalogue.release || "")}
          artist={catalogue}
          close={() =>
            // Return to the results that opened this artist, query included.
            history.length > 1 ? history.back() : go("/search")
          }
          notify={setNotice}
          initialRelease={catalogue.release}
          initialGroup={catalogue.group}
          owner={owner}
        />
        {notice && <p role="status">{notice}</p>}
      </main>
    );
  if (!library)
    return (
      <main className="personal-main">
        <p role={error ? "alert" : "status"}>
          {error || "Loading your library…"}
        </p>
      </main>
    );
  // While the next page loads, the previous one is not shown: its Play
  // would start the old list (#97).
  const wanted =
    !searching && id && ["album", "artist", "playlist"].includes(kind)
      ? `${kind}:${id}`
      : "";
  const waiting = !!wanted && detail?.route !== wanted;
  if (waiting)
    return (
      <main className="personal-main">
        <button className="text-button back-link" onClick={backToLibrary}>
          <ArrowLeft size={16} aria-hidden="true" /> Library
        </button>
        <p role={error ? "alert" : "status"}>{error || "Loading…"}</p>
      </main>
    );
  const counts: Record<string, number> = {
    musicals: library.stats.musicals,
    soundtracks: library.stats.soundtracks,
    compilations: library.stats.compilations,
  };
  const owned = detail?.tracks || [];
  const albumRows =
    detail?.quality?.scope === "album" &&
    release &&
    release.id === detail.releaseId
      ? mergeAlbumTracks(owned, release.tracks)
      : null;
  const shownTracks = albumRows || detail?.tracks;
  const missing = (albumRows || []).filter(
    (t) => !owned.includes(t) && t.server_state !== "requested",
  );
  const visibleTabs = libraryTabs.filter((id) => counts[id] !== 0);
  return (
    <main className="personal-main">
      {detail && !searching && !detail.artist && (
        <button className="text-button back-link" onClick={backToLibrary}>
          <ArrowLeft size={16} aria-hidden="true" /> Library
        </button>
      )}
      {searching ? (
        <PageHeader
          title="Search"
          subtitle="Your library, the server and the wider catalogue."
        />
      ) : detail?.artist ? (
        <ArtistHero
          artist={detail.artist}
          back={{ label: "Library", go: backToLibrary }}
        />
      ) : detail?.albumInfo ? (
        <div className="page-heading catalogue-heading">
          <Cover title={detail.title} src={detail.cover} />
          <div>
            <div className="eyebrow">IN YOUR LIBRARY</div>
            <h1>{detail.title}</h1>
            <p>{albumMeta(detail.albumInfo)}</p>
          </div>
        </div>
      ) : detail ? (
        <h1>{detail.title}</h1>
      ) : (
        <PageHeader
          title="Your library"
          subtitle={`${user.display_name} · ${formatCount(library.stats.tracks)} songs`}
        />
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {undo && (
        <p className="personal-status" role="status">
          Removed from your library.{" "}
          <button
            onClick={() =>
              void action(async () => {
                await api(`/v1/library/removals/${undo.id}/undo`, {
                  method: "POST",
                });
                setUndo(null);
                backToLibrary();
              }, "Restored")
            }
          >
            Undo
          </button>
        </p>
      )}
      {searching ? (
        <SearchPage
          query={route.params.get("q") || ""}
          library={library}
          songs={(tracks) => songs(tracks)}
        />
      ) : detail ? (
        <>
          {(detail.playlist || detail.quality) && (
            <div className="release-actions">
              {[false, true].map((shuffle) => {
                const playable = detail.playlist
                  ? playlistSongs(detail.playlist.entries, detail.cover)
                  : (detail.tracks || [])
                      .filter((t) => t.available !== false)
                      .map((t) => fromTrack(t, detail.cover));
                return (
                  <button
                    key={String(shuffle)}
                    className={shuffle ? "round-action" : "primary-button"}
                    aria-label={shuffle ? "Shuffle" : undefined}
                    title={shuffle ? "Shuffle" : undefined}
                    disabled={!playable.length}
                    onClick={() => {
                      const queue = shuffle ? shuffled(playable) : playable;
                      player.play(queue, 0);
                    }}
                  >
                    {shuffle ? (
                      <Shuffle size={18} aria-hidden="true" />
                    ) : (
                      <>
                        <Play
                          size={16}
                          fill="currentColor"
                          aria-hidden="true"
                        />{" "}
                        Play
                      </>
                    )}
                  </button>
                );
              })}
              {detail.artist && <FindMore artist={detail.artist} />}
              {albumRows && missing.length > 0 && (
                <button
                  className="round-action"
                  aria-label={`Download ${missing.length} missing songs`}
                  title="Download missing songs"
                  disabled={downloading}
                  onClick={() => void downloadMissing(missing)}
                >
                  <Download size={18} aria-hidden="true" />
                </button>
              )}
            </div>
          )}
          {detail.albums && (
            <>
              {detail.albums.length > 0 && <h2>Albums</h2>}
              <div className="personal-grid">
                {detail.albums.map((a) => (
                  <button
                    key={a.id}
                    onClick={() => go("/library/album/" + a.id)}
                  >
                    <Cover title={a.title} src={a.cover_url} />
                    {/* A button holds phrasing content only (#97). */}
                    <strong className="grid-card-title">{a.title}</strong>
                    <span className="grid-card-detail">
                      {[
                        a.year ? String(a.year) : "",
                        `${a.track_count} ${a.track_count === 1 ? "song" : "songs"}`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </button>
                ))}
              </div>
              {!!detail.tracks?.length && <h2>Songs</h2>}
            </>
          )}
          {albumRows && (
            <p className="muted album-summary">
              {albumSummary(albumRows, owned)}
            </p>
          )}
          {shownTracks && songs(shownTracks, detail.playlist)}
          {/* Per-item settings follow the music; owner tools come last. */}
          {detail.quality && (
            <section className="detail-settings" aria-label="Settings">
              <PersonalQuality
                key={detail.quality.itemId}
                {...detail.quality}
              />
            </section>
          )}
          {detail.playlist && !detail.playlist.favourites && (
            <details className="playlist-options">
              <summary>Playlist options</summary>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const name = new FormData(e.currentTarget).get("name");
                  void action(async () => {
                    const p = detail.playlist!;
                    await api(`/v1/playlists/${p.id}`, {
                      method: "PATCH",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ revision: p.revision, name }),
                    });
                    await openPlaylist(p.id);
                  });
                }}
              >
                <label>
                  Playlist name
                  <input
                    name="name"
                    defaultValue={detail.playlist.name}
                    required
                    maxLength={100}
                  />
                </label>
                <button type="submit">Rename</button>
              </form>
              <button
                onClick={() => {
                  if (
                    confirm(
                      "Delete this playlist? Music files stay in your library.",
                    )
                  )
                    void action(async () => {
                      await api(
                        `/v1/playlists/${detail.playlist!.id}?revision=${detail.playlist!.revision}`,
                        { method: "DELETE" },
                      );
                      backToLibrary();
                    }, "Playlist deleted");
                }}
              >
                Delete playlist
              </button>
            </details>
          )}
          {owner && detail.quality?.scope === "album" && (
            <OwnerAlbumTools id={detail.quality.itemId} title={detail.title} />
          )}
          {owner && detail.quality?.scope === "artist" && (
            <OwnerArtistTools id={detail.quality.itemId} title={detail.title} />
          )}
        </>
      ) : (
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
                      <Cover
                        title={artist.name}
                        src={artist.cover_url}
                        artist
                      />
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
                  void action(
                    () =>
                      api("/v1/playlists", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                          name: new FormData(form).get("name"),
                        }),
                      }),
                    "Playlist created",
                  ).then(() => form.reset());
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
      )}
      {removal && (
        <dialog ref={dialog} onCancel={() => setRemoval(null)}>
          <h2>Remove from your library?</h2>
          <p>
            {removal.preview.delete_count
              ? `${removal.preview.delete_count} music files will move to recoverable trash on the server.`
              : "The music files will stay on the server."}
          </p>
          {removal.preview.items
            .filter((i) => !i.delete_file)
            .map((i) => (
              <p key={i.track_id}>
                Kept for {i.kept_for.join(", ") || "another pending request"}.
              </p>
            ))}
          <button onClick={() => setRemoval(null)}>Cancel</button>
          <button
            onClick={() =>
              void action(async () => {
                const r = await api<{ id: string; undo_until: number }>(
                  "/v1/library/remove",
                  {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      track_ids: removal.ids,
                      confirmation: removal.preview.confirmation,
                    }),
                  },
                );
                setUndo(r);
                setRemoval(null);
                // Leave the page the songs were removed from, address too (#97).
                if (openRoute.current) backToLibrary();
                else setDetail(null);
              }, "Removed from your library")
            }
          >
            Remove
          </button>
        </dialog>
      )}
    </main>
  );
}
