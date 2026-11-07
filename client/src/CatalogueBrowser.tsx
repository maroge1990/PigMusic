import { apiFetch, peekCatalogue } from "./api";
import { useEffect, useRef, useState } from "react";
import { formatDate } from "./format";
import {
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
  ArrowRight,
  ArrowDown,
  Check,
  Play,
  Shuffle,
} from "lucide-react";
import { Cover, catalogueCover } from "./Cover";
import { PageHeader, SectionHeader } from "./PageHeader";
import { ArtistPopular } from "./ArtistPopular";
import { MusicLink } from "./MusicLink";
import { mergePage } from "./discography";
import { ArtistAcquisition } from "./ArtistAcquisition";
import { OwnerPanel } from "./OwnerTools";
import { FollowArtist } from "./FollowArtist";
import { ArtistHero } from "./ArtistHero";
import { discoverUrl } from "./navigation";
import type { Discography } from "./discography";
import { SongRow } from "./SongRow";
import { usePlayer } from "./Player";
import { shuffled } from "./shuffle";
import { songBody, withLibrary, type Song as Row } from "./songs";
import { EmptyLine, InlineError, Loading } from "./EmptyState";

function releaseSongs(release: Release, artistId: string): Row[] {
  const cover = catalogueCover("release", release.id);
  return release.tracks.map((t) => ({
    key: release.id + ":" + t.id,
    title: t.title,
    artist: t.artist,
    album: release.title,
    album_artist: release.artist,
    duration: t.duration,
    year: t.year,
    track_number: t.track_number,
    disc_number: t.disc_number,
    cover,
    state: pending(t) ? "pending" : "missing",
    release_id: release.id,
    track_id: t.id,
    recording_id: t.musicbrainz_recording_id,
    artist_id: artistId,
    explicit_edition: t.explicit_edition,
    recording_kind: t.recording_kind,
  }));
}

type Group = {
  id: string;
  title: string;
  date: string;
  type: string;
  /** Your library album for this release, and how many of its songs you have. */
  saved_album_id?: string | null;
  saved_songs?: number;
};
type Edition = {
  id: string;
  title: string;
  date: string;
  country: string;
  description: string;
  status: string;
  formats: string[];
  track_count: number;
};
type Song = {
  id: string;
  title: string;
  artist: string;
  duration: number;
  year?: string | number;
  disc_number: number;
  track_number: number;
  status: string;
  presence_note: string;
  download_status?: string;
  received_bytes?: number;
  total_bytes?: number;
  musicbrainz_recording_id?: string;
  /** On an edition other than the recommended one: requests keep it. */
  explicit_edition?: boolean;
  /** A Live, Remix or Demo release's songs are those recordings. */
  recording_kind?: string[];
};
type Release = {
  id: string;
  title: string;
  artist: string;
  date: string;
  description: string;
  tracks: Song[];
};
// Request states still in progress on the server.
const pending = (song: { download_status?: string }) =>
  [
    "waiting_for_sources",
    "searching",
    "downloading",
    "validating",
    "importing",
  ].includes(song.download_status || "");
export function CatalogueBrowser({
  artist,
  close,
  notify,
  initialRelease,
  initialGroup,
  owner = false,
}: {
  artist: { id: string; name: string };
  close: () => void;
  notify: (text: string) => void;
  initialRelease?: string;
  initialGroup?: string;
  owner?: boolean;
}) {
  async function request<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await apiFetch("/api/v1/catalogue/" + path, options);
    const data = await response.json();
    if (!response.ok)
      throw new Error(
        typeof data.detail === "string" ? data.detail : "Catalogue unavailable",
      );
    return data;
  }
  const [otherReleases, setOtherReleases] = useState(false);
  const cachedPopularity = peekCatalogue<{
    ranked_album_ids?: string[];
    albums: Group[];
  }>(`/api/v1/catalogue/artists/${artist.id}/popularity`);
  const [rankedAlbums, setRankedAlbums] = useState<string[]>(
    () =>
      cachedPopularity?.ranked_album_ids ||
      cachedPopularity?.albums.map((a) => a.id) ||
      [],
  );
  useEffect(() => {
    setRankedAlbums(
      cachedPopularity?.ranked_album_ids ||
        cachedPopularity?.albums.map((a) => a.id) ||
        [],
    );
    void request<{ ranked_album_ids?: string[]; albums: Group[] }>(
      `artists/${artist.id}/popularity`,
    )
      .then((d) =>
        setRankedAlbums(d.ranked_album_ids || d.albums.map((a) => a.id)),
      )
      .catch(() => {});
  }, [artist.id]);
  const [kind, setKind] = useState("album");
  const scope = artist.id + ":" + kind;
  const cachedListing = peekCatalogue<{ releases: Group[]; total: number }>(
    `/api/v1/catalogue/artists/${artist.id}/releases?kind=${kind}&offset=0&name=${encodeURIComponent(artist.name)}`,
  );
  const [listing, setListing] = useState<Discography>(() => ({
    scope,
    groups: cachedListing?.releases || [],
    total: cachedListing?.total || 0,
    loading: !cachedListing,
  }));
  const [visible, setVisible] = useState(100);
  const [group, setGroup] = useState<Group | null>(null);
  const albumRow = useRef<HTMLDivElement>(null);
  const [albumSize, setAlbumSize] = useState(3);
  const [albumPage, setAlbumPage] = useState(0);
  useEffect(() => {
    setAlbumPage(0);
  }, [scope]);
  useEffect(() => {
    const node = albumRow.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      setAlbumSize(
        Math.max(
          1,
          Math.min(5, Math.floor((entry.contentRect.width + 20) / 180)),
        ),
      );
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [group, otherReleases]);

  const allGroups = listing.scope === scope ? listing.groups : [];
  const ranks = new Map(rankedAlbums.map((id, i) => [id, i]));
  const groups =
    kind === "album"
      ? [...allGroups].sort(
          (a, b) =>
            (ranks.get(a.id) ?? Infinity) - (ranks.get(b.id) ?? Infinity) ||
            (a.date || "").localeCompare(b.date || "") ||
            a.title.localeCompare(b.title),
        )
      : allGroups;
  const total = listing.scope === scope ? listing.total : 0;
  const currentAlbumPage = Math.min(
    albumPage,
    Math.max(0, Math.ceil(groups.length / albumSize) - 1),
  );
  const albumStart = currentAlbumPage * albumSize;

  const [editions, setEditions] = useState<Edition[]>([]);
  const [editionTotal, setEditionTotal] = useState(0);
  const [release, setRelease] = useState<Release | null>(null);
  const [choose, setChoose] = useState(false);
  const player = usePlayer();
  const [rows, setRows] = useState<Row[]>([]);
  useEffect(() => {
    if (!release) return setRows([]);
    const plain = releaseSongs(release, artist.id);
    setRows(plain);
    let active = true;
    void withLibrary(plain)
      .then((found) => active && setRows(found))
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [release]);
  const [busy, setBusy] = useState(false);
  const groupBusy = listing.scope !== scope || listing.loading;
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setListing((old) =>
      old.scope === scope
        ? { ...old, loading: true }
        : {
            scope,
            groups: cachedListing?.releases || [],
            total: cachedListing?.total || 0,
            loading: true,
          },
    );
    setGroup(null);
    setRelease(null);
    setError("");
    void (async () => {
      try {
        let offset = 0;
        while (!controller.signal.aborted) {
          const data = await request<{ releases: Group[]; total: number }>(
            // The name lets the server find your copies without a lookup.
            `artists/${artist.id}/releases?kind=${kind}&offset=${offset}&name=${encodeURIComponent(artist.name)}`,
            { signal: controller.signal },
          );
          if (controller.signal.aborted) return;
          setListing((old) =>
            controller.signal.aborted
              ? old
              : mergePage(old, scope, data.releases, data.total),
          );
          offset += data.releases.length;
          if (
            !data.releases.length ||
            offset >= data.total ||
            offset >= visible ||
            offset >= 10000
          )
            break;
        }
      } catch (e) {
        if (!controller.signal.aborted) setError((e as Error).message);
      } finally {
        if (!controller.signal.aborted)
          setListing((old) =>
            old.scope === scope ? { ...old, loading: false } : old,
          );
      }
    })();
    return () => controller.abort();
  }, [artist.id, kind, visible]);
  useEffect(() => {
    if (!release || !release.tracks.some(pending)) return;
    const id = release.id;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    // Start at 3 s, then wait half as long again each time, up to 30 s; a
    // song finishing restarts this effect and the quick checks (#97).
    let delay = 3000;
    const check = () => {
      timer = setTimeout(() => {
        delay = Math.min(30000, delay * 1.5);
        if (document.hidden) return check();
        void request<Release>(`releases/${id}`)
          .then((data) => {
            if (active) setRelease(data);
          })
          .catch(() => {})
          .finally(() => {
            if (active) check();
          });
      }, delay);
    };
    check();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [release?.id, release?.tracks.filter(pending).length]);
  useEffect(() => {
    if (initialRelease) {
      setGroup({ id: "", title: "Album", date: "", type: "Album" });
      void loadRelease(initialRelease);
    }
  }, [initialRelease, artist.id]);
  useEffect(() => {
    if (initialGroup)
      void openGroup({
        id: initialGroup,
        title: "Album",
        date: "",
        type: "Album",
      });
  }, [initialGroup, artist.id]);
  async function loadRelease(id: string) {
    setBusy(true);
    setError("");
    try {
      setRelease(await request<Release>(`releases/${id}`));
      history.replaceState(null, "", "#" + discoverUrl(artist, id));
      setChoose(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function openGroup(g: Group) {
    setGroup(g);
    setRelease(null);
    setChoose(false);
    setBusy(true);
    setError("");
    try {
      const data = await request<{
        editions: Edition[];
        total: number;
        recommended_id?: string;
      }>(`groups/${g.id}/editions`);
      setEditions(data.editions);
      setEditionTotal(data.total);
      if (data.editions.length) {
        setRelease(
          await request<Release>(
            `releases/${data.recommended_id || data.editions[0].id}`,
          ),
        );
        history.replaceState(
          null,
          "",
          "#" + discoverUrl(artist, data.recommended_id || data.editions[0].id),
        );
      } else setError("No release editions are available.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function moreEditions() {
    if (!group) return;
    setBusy(true);
    try {
      const data = await request<{ editions: Edition[]; total: number }>(
        `groups/${group.id}/editions?offset=${editions.length}`,
      );
      setEditions((old) => [...old, ...data.editions]);
      setEditionTotal(data.total);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function download() {
    if (!release) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch("/api/v1/requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tracks: rows.map(songBody) }),
      });
      const result = await response.json();
      if (!response.ok)
        throw new Error(
          typeof result.detail === "string"
            ? result.detail
            : "Could not request this album. Please try again.",
        );
      notify("Downloading to your library");
      setRows((old) =>
        old.map((r) =>
          r.state === "missing" ? { ...r, state: "pending" } : r,
        ),
      );
      window.dispatchEvent(new Event("music-library-changed"));
      window.dispatchEvent(new Event("music-requests-changed"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function back() {
    setError("");
    if (choose) {
      setChoose(false);
    } else if (group) {
      setGroup(null);
      setRelease(null);
      history.replaceState(null, "", "#" + discoverUrl(artist));
    } else close();
  }
  return (
    <section aria-label="Artist discography">
      {group && (
        <button
          className="text-button back-link"
          onClick={back}
          disabled={busy}
        >
          <ArrowLeft size={16} aria-hidden="true" />
          {choose ? "Selected edition" : artist.name}
        </button>
      )}
      {!group && (
        <ArtistHero
          back={{ label: "Back", go: back }}
          discovered
          artist={{
            ...artist,
            album_ids: [],
            album_count: 0,
            track_count: 0,
            compilation_only: false,
            cover_url: `/api/v1/catalogue/artists/${artist.id}/artwork`,
          }}
        />
      )}
      {group && (
        <PageHeader
          cover={
            <Cover
              title={release?.title || group.title}
              src={catalogueCover(
                release ? "release" : "release-group",
                release?.id || group.id,
              )}
            />
          }
          eyebrow={release ? "ALBUM & SONGS" : "ARTIST"}
          title={release?.title || group?.title || artist.name}
          subtitle={
            release ? (
              <>
                <MusicLink artist={release.artist} /> ·{" "}
                {formatDate(release.date)} {release.description}
              </>
            ) : (
              "Albums first. Explore singles, EPs and other releases below."
            )
          }
        />
      )}
      {error && <InlineError>{error}</InlineError>}
      {busy && <Loading>Loading release…</Loading>}
      {choose ? (
        <>
          <SectionHeader title="Choose another edition" />
          {editions.map((e) => (
            <button
              className="result-row"
              key={e.id}
              disabled={busy}
              onClick={() => loadRelease(e.id)}
            >
              <Cover title={e.title} src={catalogueCover("release", e.id)} />
              <div>
                <strong>{e.title}</strong>
                <span>
                  {[
                    formatDate(e.date),
                    e.country,
                    e.formats.join(" / "),
                    `${e.track_count} tracks`,
                    e.description,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </div>
              <ArrowRight size={16} />
            </button>
          ))}
          {editions.length < editionTotal && (
            <button
              className="secondary-button"
              disabled={busy}
              onClick={moreEditions}
            >
              More editions
            </button>
          )}
        </>
      ) : release ? (
        <>
          <div className="release-actions">
            <button
              className="primary-button"
              disabled={!rows.length}
              onClick={() => player.play(rows, 0)}
            >
              <Play size={16} fill="currentColor" /> Play
            </button>
            <button
              className="round-action"
              aria-label="Shuffle"
              title="Shuffle"
              disabled={!rows.length}
              onClick={() => player.play(shuffled(rows), 0)}
            >
              <Shuffle size={18} aria-hidden="true" />
            </button>
            <button
              className="round-action"
              aria-label="Download album"
              title="Download album"
              disabled={busy || !rows.some((r) => r.state === "missing")}
              onClick={() => download()}
            >
              <ArrowDown size={18} aria-hidden="true" />
            </button>
            {group?.saved_album_id && (
              <a
                className="secondary-button"
                href={"#/library/album/" + group.saved_album_id}
              >
                <Check size={16} aria-hidden="true" /> Open in your library
              </a>
            )}
          </div>
          {rows.length > 0 && !rows.some((r) => r.state === "missing") && (
            <p className="muted">
              {rows.every((r) => r.state === "saved")
                ? "Already in your library: nothing to download."
                : "Nothing to download: every song is in your library, on the server or requested."}
            </p>
          )}
          <div className="song-list">
            {rows.map((row, i) => (
              <SongRow
                key={row.key}
                song={row}
                queue={rows}
                index={i}
                numbered
                onAlbum
                onArtist={
                  row.artist.toLocaleLowerCase() ===
                  artist.name.toLocaleLowerCase()
                }
              />
            ))}
          </div>
          {editionTotal > 0 && (
            <button
              className="secondary-button edition-switch"
              onClick={() => setChoose(true)}
            >
              Choose another edition · {editionTotal} available
            </button>
          )}
        </>
      ) : !group ? (
        <>
          {!otherReleases && (
            <ArtistPopular id={artist.id} openAlbum={openGroup} songsOnly />
          )}
          {
            <div className="section-title">
              <h2>
                {otherReleases ? "Singles, EPs & other releases" : "All albums"}
              </h2>
              {!otherReleases && groups.length > 0 && (
                <div className="popular-pager">
                  <span aria-live="polite">
                    {albumStart + 1}–
                    {Math.min(albumStart + albumSize, groups.length)} of {total}
                  </span>
                  <button
                    className="icon-button"
                    aria-label="Previous albums"
                    disabled={currentAlbumPage === 0}
                    onClick={() => setAlbumPage(currentAlbumPage - 1)}
                  >
                    <ChevronLeft size={18} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Next albums"
                    disabled={
                      albumStart + albumSize >= groups.length &&
                      (groups.length >= total || groupBusy)
                    }
                    onClick={() => {
                      if (
                        albumStart + albumSize >= groups.length &&
                        groups.length < total
                      )
                        setVisible((n) => n + 100);
                      setAlbumPage(currentAlbumPage + 1);
                    }}
                  >
                    <ChevronRight size={18} />
                  </button>
                </div>
              )}
              <span>
                {otherReleases
                  ? "Full catalogue"
                  : rankedAlbums.length
                    ? "Popular order"
                    : "Chronological order"}
              </span>
            </div>
          }
          {otherReleases && (
            <div
              className="tab-list discography-tabs"
              role="tablist"
              aria-label="Release types"
            >
              {[
                ["single", "Singles"],
                ["ep", "EPs"],
                ["other", "Other releases"],
              ].map(([id, label]) => (
                <button
                  role="tab"
                  aria-selected={kind === id}
                  className={kind === id ? "active" : ""}
                  key={id}
                  onClick={() => {
                    setListing({
                      scope: artist.id + ":" + id,
                      groups: [],
                      total: 0,
                      loading: true,
                    });
                    setVisible(100);
                    setKind(id);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          <p className="section-description">
            {otherReleases
              ? "Newest first"
              : rankedAlbums.length
                ? "Popular first"
                : "Oldest first"}{" "}
            · {groups.length}
            {total > groups.length ? ` of ${total}` : ""} releases
            {groupBusy ? " · loading…" : ""}
          </p>
          <div
            ref={albumRow}
            className={
              otherReleases ? "release-list" : "full-album-grid album-carousel"
            }
            style={
              otherReleases
                ? undefined
                : {
                    gridTemplateColumns: `repeat(${albumSize}, minmax(0, 1fr))`,
                  }
            }
          >
            {(otherReleases
              ? groups.slice(0, visible)
              : groups.slice(albumStart, albumStart + albumSize)
            ).map((g) => (
              <button
                className={otherReleases ? "result-row" : "album-card"}
                key={scope + ":" + g.id}
                disabled={busy}
                onClick={() => openGroup(g)}
              >
                <Cover
                  title={g.title}
                  src={catalogueCover("release-group", g.id)}
                />
                <div>
                  <strong>{g.title}</strong>
                  <span>
                    {[formatDate(g.date), g.type].filter(Boolean).join(" · ")}
                  </span>
                </div>
                {g.saved_album_id ? (
                  <span
                    className="saved-icon discography-saved"
                    title={`${g.saved_songs ?? 0} of your songs`}
                  >
                    <Check size={14} />
                    In library
                  </span>
                ) : (
                  <ArrowRight size={16} />
                )}
              </button>
            ))}
          </div>
          <button
            className="secondary-button"
            onClick={() => {
              setOtherReleases(!otherReleases);
              setKind(otherReleases ? "album" : "single");
              setVisible(100);
            }}
          >
            {otherReleases
              ? "Back to artist albums"
              : "Singles, EPs & other releases"}
            <ArrowRight size={16} />
          </button>
          {otherReleases && groups.length < total && (
            <button
              className="secondary-button"
              onClick={() => setVisible((n) => n + 100)}
            >
              More releases
            </button>
          )}
          {!groupBusy && !groups.length && !error && (
            <EmptyLine>No releases in this section.</EmptyLine>
          )}
        </>
      ) : null}
      {!group && (
        <section className="detail-settings" aria-label="Artist settings">
          <FollowArtist id={artist.id} name={artist.name} />
        </section>
      )}
      {owner && !group && (
        <OwnerPanel label="server downloads for this artist">
          <ArtistAcquisition id={artist.id} notify={notify} />
        </OwnerPanel>
      )}
    </section>
  );
}
