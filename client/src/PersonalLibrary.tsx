import { FindMore } from "./FindMore";
import { ArtistHero } from "./ArtistHero";
import { CatalogueBrowser } from "./CatalogueBrowser";
import { Shuffle } from "lucide-react";
import { shuffled } from "./shuffle";
import { useEffect, useRef, useState } from "react";
import { Play, ArrowLeft, Download } from "lucide-react";
import { api, apiFetch } from "./api";
import { defer, isPending } from "./deferred";
import { offerUndo } from "./UndoBar";
import { formatCount } from "./format";
import { PageHeader } from "./PageHeader";
import { PersonalQuality } from "./PersonalQuality";
import { SearchPage } from "./SearchPage";
import { Cover } from "./Cover";
import { fromTrack, playlistSongs, songBody } from "./songs";
import { albumSummary, mergeAlbumTracks } from "./albumTracks";
import { warmCovers, whenIdle } from "./prefetch";
import { usePlayer } from "./Player";
import type { Album, Artist, Library, Track } from "./types";
import type { Person } from "./AppShell";
import {
  LibraryCollections,
  libraryTabs,
  type LibraryTab,
} from "./LibraryCollections";
import type { Playlist, PlaylistSummary } from "./libraryTypes";
import { LibrarySongs } from "./LibrarySongs";
import { ArtistAlbums } from "./ArtistAlbums";
import { PlaylistOptions } from "./PlaylistOptions";
import {
  RemovalDialog,
  UndoRemoval,
  type Removal,
  type RemovalPreview,
} from "./RemoveFromLibrary";
import { go, type Route } from "./routes";
import { OwnerAlbumTools, OwnerArtistTools } from "./OwnerTools";
import { InlineError, Loading } from "./EmptyState";

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
  const [removal, setRemoval] = useState<Removal | null>(null);
  const [undo, setUndo] = useState<{ id: string; undo_until: number } | null>(
    null,
  );
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
    // A playlist whose deletion is waiting for its Undo time is gone already.
    setPlaylists(list.playlists.filter((p) => !isPending("playlist:" + p.id)));
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
      const data = await api<RemovalPreview>("/v1/library/removal-preview", {
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
    return (
      <LibrarySongs
        key={detail?.title || tab}
        tracks={tracks}
        playlist={playlist}
        cover={detail?.cover}
        albumPage={detail?.quality?.scope === "album"}
        artistName={detail?.artist?.name}
        remove={(id) => void preview([id])}
        moveUp={(playlist, entry) =>
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
        removeFromPlaylist={(playlist, entry) =>
          void action(async () => {
            await api(
              `/v1/playlists/${playlist.id}/songs/${entry.position}?revision=${playlist.revision}`,
              { method: "DELETE" },
            );
            await openPlaylist(playlist.id);
          })
        }
      />
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
        {error ? (
          <InlineError>{error}</InlineError>
        ) : (
          <Loading>Loading your library…</Loading>
        )}
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
        {error ? <InlineError>{error}</InlineError> : <Loading />}
      </main>
    );
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
        <PageHeader
          cover={<Cover title={detail.title} src={detail.cover} />}
          eyebrow="IN YOUR LIBRARY"
          title={detail.title}
          subtitle={albumMeta(detail.albumInfo)}
        />
      ) : detail ? (
        <PageHeader title={detail.title} />
      ) : (
        <PageHeader
          title="Your library"
          subtitle={`${user.display_name} · ${formatCount(library.stats.tracks)} songs`}
        />
      )}
      {error && <InlineError>{error}</InlineError>}
      {notice && <p role="status">{notice}</p>}
      {undo && (
        <UndoRemoval
          undo={() =>
            void action(async () => {
              await api(`/v1/library/removals/${undo.id}/undo`, {
                method: "POST",
              });
              setUndo(null);
              backToLibrary();
            }, "Restored")
          }
        />
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
            <ArtistAlbums
              albums={detail.albums}
              hasSongs={!!detail.tracks?.length}
            />
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
            <PlaylistOptions
              playlist={detail.playlist}
              rename={(name) =>
                void action(async () => {
                  const p = detail.playlist!;
                  await api(`/v1/playlists/${p.id}`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ revision: p.revision, name }),
                  });
                  await openPlaylist(p.id);
                })
              }
              remove={() => {
                // Leaves at once; sent after the Undo time (D-79, #183).
                const p = detail.playlist!;
                const pending = defer("playlist:" + p.id, (keepalive) =>
                  apiFetch(`/api/v1/playlists/${p.id}?revision=${p.revision}`, {
                    method: "DELETE",
                    keepalive,
                  }).then(async (r) => {
                    if (!r.ok)
                      throw new Error(
                        (await r.json().catch(() => ({}))).detail ||
                          "The playlist couldn't be deleted.",
                      );
                  }),
                );
                setPlaylists((old) => old.filter((v) => v.id !== p.id));
                backToLibrary();
                offerUndo(
                  `${p.name} deleted.`,
                  async () => {
                    if (!pending.undo())
                      throw new Error("It was deleted already.");
                    await refresh();
                  },
                  `${p.name} is back.`,
                );
                pending.done.then(
                  (sent) => sent && void refresh(),
                  (e: Error) => {
                    setError(e.message);
                    void refresh();
                  },
                );
              }}
            />
          )}
          {owner && detail.quality?.scope === "album" && (
            <OwnerAlbumTools id={detail.quality.itemId} title={detail.title} />
          )}
          {owner && detail.quality?.scope === "artist" && (
            <OwnerArtistTools id={detail.quality.itemId} title={detail.title} />
          )}
        </>
      ) : (
        <LibraryCollections
          library={library}
          tab={tab}
          owner={owner}
          playlists={playlists}
          favouriteList={favouriteList}
          songs={songs}
          createPlaylist={(name) =>
            action(
              () =>
                api("/v1/playlists", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ name }),
                }),
              "Playlist created",
            )
          }
          refresh={refresh}
        />
      )}
      {removal && (
        <RemovalDialog
          removal={removal}
          cancel={() => setRemoval(null)}
          confirm={() =>
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
        />
      )}
    </main>
  );
}
