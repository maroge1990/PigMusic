import { isUnconfirmed, peekCatalogue } from "./api";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { formatDate } from "./format";
import {
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
  ArrowRight,
  CircleCheck,
  Layers,
  Play,
  Plus,
  Shuffle,
} from "lucide-react";
import { Cover, catalogueCover } from "./Cover";
import { PageHeader, SectionHeader } from "./PageHeader";
import { ArtistPopular } from "./ArtistPopular";
import { CatalogueAlbumCard } from "./CatalogueCards";
import {
  NO_EDITIONS,
  catalogueRequest,
  requestAlbum,
} from "./catalogueActions";
import { MusicLink } from "./MusicLink";
import { mergePage } from "./discography";
import { ArtistAcquisition } from "./ArtistAcquisition";
import { notify, notifyError } from "./notices";
import { OwnerPanel } from "./OwnerTools";
import { FollowArtist } from "./FollowArtist";
import { ArtistHero } from "./ArtistHero";
import { discoverUrl } from "./navigation";
import type { Discography } from "./discography";
import { SongRow } from "./SongRow";
import { usePlayer } from "./Player";
import { shuffled } from "./shuffle";
import { withLibrary, type Song as Row } from "./songs";
import {
  addable,
  addedMessage,
  checking,
  lookedUp,
  markYours,
  nothingToAdd,
  openingEdition,
  pending,
  releaseSongs,
  type CatalogueRelease as Release,
  type Editions,
} from "./catalogueAlbum";
import { EmptyLine, InlineError, Loading } from "./EmptyState";

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
export function CatalogueBrowser({
  artist,
  close,
  initialRelease,
  initialGroup,
  owner = false,
}: {
  artist: { id: string; name: string };
  close: () => void;
  initialRelease?: string;
  initialGroup?: string;
  owner?: boolean;
}) {
  const request = catalogueRequest;
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
  // Try again after a failed library lookup asks again.
  const [lookups, setLookups] = useState(0);
  useEffect(() => {
    if (!release) return setRows([]);
    const plain = releaseSongs(
      release,
      artist.id,
      catalogueCover("release", release.id),
    );
    // A song matches any edition unless it is an explicit edition: the
    // library lookup may mark the others yours (a compilation copy counts),
    // but an explicit edition keeps the release answer's state. Until the
    // lookup answers, those songs can't be added: one you have on another
    // album would be added again (#263).
    setRows(checking(plain));
    let active = true;
    void withLibrary(lookedUp(plain))
      .then((found) => {
        if (!active) return;
        setRows(markYours(plain, found));
      })
      .catch(() => {
        // Add stays off until the lookup answers (#263).
        if (active)
          notifyError(
            "Couldn't check your library. Try again.",
            () => setLookups((n) => n + 1),
            "library-lookup",
          );
      });
    return () => {
      active = false;
    };
  }, [release, lookups]);
  // Which release is on screen, and since when: a navigation (another
  // album, another edition, back) starts a new view (#262). Within a view,
  // its reads are numbered as they start (`started`), and `applied` is the
  // newest one shown.
  const shown = useRef({ id: "", generation: 0, started: 0, applied: 0 });
  useLayoutEffect(() => {
    shown.current = {
      id: release?.id || "",
      generation: shown.current.generation + 1,
      started: 0,
      applied: 0,
    };
  }, [release?.id]);
  /** Every background read of a release (a poll, the read after an Add, a
   * row's refresh) goes through here. A read for a release no longer on
   * screen is not made, and an answer is shown only in the view that asked
   * and only when nothing that started later has been shown: an older
   * answer would undo a newer song's state (#262, #263). */
  function refreshRelease(id: string) {
    const view = shown.current;
    if (view.id !== id) return Promise.resolve();
    const sequence = ++view.started;
    return request<Release>(`releases/${id}`)
      .then((data) => {
        if (shown.current !== view || sequence <= view.applied) return;
        view.applied = sequence;
        setRelease(data);
      })
      .catch(() => {});
  }
  function reloadRelease() {
    if (release) void refreshRelease(release.id);
  }
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
        void refreshRelease(id).finally(() => {
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
      // The edition on screen chosen again: this read takes its turn with
      // the view's other reads, so an older poll can't undo it.
      const view = shown.current;
      const sequence = view.id === id ? ++view.started : 0;
      const data = await request<Release>(`releases/${id}`);
      if (!sequence) setRelease(data);
      else if (shown.current === view && sequence > view.applied) {
        view.applied = sequence;
        setRelease(data);
      }
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
      const data = await request<Editions<Edition>>(`groups/${g.id}/editions`);
      setEditions(data.editions);
      setEditionTotal(data.total);
      const opening = openingEdition(data);
      if (opening) {
        setRelease(await request<Release>(`releases/${opening}`));
        history.replaceState(null, "", "#" + discoverUrl(artist, opening));
      } else setError(NO_EDITIONS);
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
      notify(addedMessage(await requestAlbum(rows)));
      // Each song's state as the server now has it, not a guess.
      await refreshRelease(release.id);
      window.dispatchEvent(new Event("music-library-changed"));
      window.dispatchEvent(new Event("music-requests-changed"));
    } catch (e) {
      setError((e as Error).message);
      // It may have been requested: show each song's state as it is now.
      if (isUnconfirmed(e)) void refreshRelease(release.id);
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
              "Albums first. Explore singles, EPs and more below."
            )
          }
        />
      )}
      {error && <InlineError>{error}</InlineError>}
      {busy && <Loading>Loading album…</Loading>}
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
                    `${e.track_count} songs`,
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
              <Layers size={16} aria-hidden="true" /> More editions
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
              aria-label="Add to library"
              title="Add to library"
              disabled={busy || !rows.some(addable)}
              onClick={() => download()}
            >
              <Plus size={18} aria-hidden="true" />
            </button>
            {group?.saved_album_id && (
              <a
                className="secondary-button"
                href={"#/library/album/" + group.saved_album_id}
              >
                <CircleCheck size={16} aria-hidden="true" /> Open in your
                library
              </a>
            )}
          </div>
          {rows.length > 0 && !rows.some((r) => addable(r) || r.checking) && (
            <p className="muted">{nothingToAdd(rows)}</p>
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
                refresh={reloadRelease}
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
              <Layers size={16} aria-hidden="true" /> Choose another edition ·{" "}
              {editionTotal} available
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
              <h2>{otherReleases ? "Singles, EPs and more" : "All albums"}</h2>
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
              aria-label="Album types"
            >
              {[
                ["single", "Singles"],
                ["ep", "EPs"],
                ["other", "Other"],
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
            {total > groups.length ? ` of ${total}` : ""} albums
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
            ).map((g) => {
              const card = (mark?: ReactNode) => (
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
                      <CircleCheck size={14} />
                      In your library
                    </span>
                  ) : (
                    mark || <ArrowRight size={16} />
                  )}
                </button>
              );
              // The album cards get the one collection menu (D-129); the
              // list of singles, EPs and more stays rows.
              return otherReleases ? (
                card()
              ) : (
                <CatalogueAlbumCard
                  key={scope + ":" + g.id}
                  group={g.id}
                  title={g.title}
                  artistId={artist.id}
                >
                  {card}
                </CatalogueAlbumCard>
              );
            })}
          </div>
          <button
            className="secondary-button"
            onClick={() => {
              setOtherReleases(!otherReleases);
              setKind(otherReleases ? "album" : "single");
              setVisible(100);
            }}
          >
            {otherReleases ? "Back to artist albums" : "Singles, EPs and more"}
            <ArrowRight size={16} />
          </button>
          {otherReleases && groups.length < total && (
            <button
              className="secondary-button"
              onClick={() => setVisible((n) => n + 100)}
            >
              More albums
            </button>
          )}
          {!groupBusy && !groups.length && !error && (
            <EmptyLine>No albums in this section.</EmptyLine>
          )}
        </>
      ) : null}
      {!group && (
        <section className="detail-settings" aria-label="Artist settings">
          <FollowArtist id={artist.id} name={artist.name} />
        </section>
      )}
      {owner && !group && (
        <OwnerPanel label="artist monitoring">
          <ArtistAcquisition id={artist.id} />
        </OwnerPanel>
      )}
    </section>
  );
}
