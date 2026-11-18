import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Disc3,
  ListMusic,
  Music,
  Plus,
  Search,
  UserRound,
  X,
} from "lucide-react";
import { ApiError, api, currentSession } from "./api";
import { Cover, catalogueArtistCover } from "./Cover";
import { discoverUrl } from "./navigation";
import { go } from "./routes";
import { SongRow } from "./SongRow";
import {
  CollectionCard,
  albumSongs,
  artistSongs,
  playlistSongList,
} from "./CollectionMenu";
import { fromTrack } from "./songs";
import { warmArtist, whenIdle } from "./prefetch";
import { StateBadge } from "./StateBadge";
import { formatCount } from "./format";
import {
  answer,
  recentSearches,
  remember,
  saveRecentSearches,
  withRecent,
  type CatalogueArtist,
  type FoundAlbum,
  type FoundArtist,
  type Local,
} from "./searchAnswers";
import type { Library } from "./types";
import { SectionHeader } from "./PageHeader";
import { EmptyLine, InlineError, Loading } from "./EmptyState";
import "./search.css";

const fold = (value: string) => value.toLocaleLowerCase().normalize("NFKD");
const clean = (value: string) => value.trim().slice(0, 150);

/** Type-ahead searches the library this long after the last keystroke; a
 * newer query aborts the older one's request. */
const SEARCH_DEBOUNCE_MS = 150;
/** The catalogue (MusicBrainz, rate-limited upstream) is asked only once
 * typing has paused this long, so passing prefixes add no catalogue load. */
const CATALOGUE_PAUSE_MS = 350;
/** Artist pages are prepared only for a query left alone this long, so
 * passing prefixes ask the catalogue for nothing more. */
const WARM_AFTER_MS = 1500;
const CATALOGUE_MINIMUM = 2;
const SONGS_SHOWN = 8;
const ALBUMS_SHOWN = 12;
const ARTISTS_SHOWN = 8;

/** An album's one mark: in your library, adding, or not in your library. */
function AlbumMark({ album }: { album: FoundAlbum }) {
  if (album.in_library && album.server_state === "requested")
    return <StateBadge state="requested" iconOnly />;
  if (album.in_library) return <StateBadge state="library" iconOnly />;
  return (
    <span
      className="state-badge icon-only search-add-mark"
      role="img"
      aria-label="Not in your library"
      title="Not in your library"
    >
      <Plus size={20} />
    </span>
  );
}

type Candidate = { id: string; name: string; disambiguation?: string };

const plural = (n: number, one: string) =>
  `${formatCount(n)} ${one}${n === 1 ? "" : "s"}`;

/** One search: before typing, recent searches and your library; then one
 * ranked list (Top result, Songs, Albums, Artists), each item once. */
export function SearchPage({
  query,
  library,
  person,
}: {
  query: string;
  library: Library;
  /** Whose recent searches to keep on this browser. */
  person: string;
}) {
  const [text, setText] = useState(query);
  // The query being searched: the address's, then what is typed, once
  // typing pauses.
  const [active, setActive] = useState(clean(query));
  // The query the catalogue is asked for: the same, after a longer pause. A
  // new object per pause, so returning to a query asks again if need be.
  const [settled, setSettled] = useState({ query: clean(query) });
  const shown = useRef(active);
  shown.current = active;
  const typed = useRef(false);
  const [recent, setRecent] = useState(() => recentSearches(person));
  useEffect(() => setRecent(recentSearches(person)), [person]);
  const keepRecent = (q: string) => {
    const next = withRecent(recentSearches(person), q);
    saveRecentSearches(person, next);
    setRecent(next);
  };
  const forget = (q?: string) => {
    const next = q ? recent.filter((r) => r !== q) : [];
    saveRecentSearches(person, next);
    setRecent(next);
  };
  useEffect(() => {
    // Arriving from elsewhere (a link, back): the address wins.
    if (clean(query) === active) return;
    typed.current = false;
    setText(query);
    setActive(clean(query));
    setSettled({ query: clean(query) });
  }, [query]);
  useEffect(() => {
    if (!typed.current) return;
    const next = clean(text);
    const timer = setTimeout(() => {
      setActive(next);
      // Keep the address in step without a history entry per keystroke.
      history.replaceState(
        null,
        "",
        next ? "#/search?q=" + encodeURIComponent(next) : "#/search",
      );
    }, SEARCH_DEBOUNCE_MS);
    const pause = setTimeout(
      () => setSettled({ query: next }),
      CATALOGUE_PAUSE_MS,
    );
    return () => {
      clearTimeout(timer);
      clearTimeout(pause);
    };
  }, [text]);
  const [local, setLocal] = useState<Local | null>(null);
  const [catalogue, setCatalogue] = useState<CatalogueArtist[] | null>(null);
  const [catalogueFailed, setCatalogueFailed] = useState(false);
  const [error, setError] = useState("");
  const [choices, setChoices] = useState<{
    detail: string;
    candidates: Candidate[];
  } | null>(null);
  // An account change ends this person's search: what was shown goes too.
  const [session, setSession] = useState(currentSession);
  useEffect(() => {
    const ended = () => {
      setLocal(null);
      setCatalogue(null);
      setSession(currentSession());
    };
    window.addEventListener("music-session-cleared", ended);
    return () => window.removeEventListener("music-session-cleared", ended);
  }, []);
  useEffect(() => {
    setCatalogueFailed(false);
    setError("");
    setChoices(null);
    const key = fold(active);
    const saved = answer(key);
    setLocal(saved?.local ?? null);
    setCatalogue(
      active.length < CATALOGUE_MINIMUM ? [] : (saved?.catalogue ?? null),
    );
    if (!active) return;
    // A newer query, or another person, aborts this one's requests.
    const controller = new AbortController();
    const signal = controller.signal;
    const asked = currentSession();
    const current = () => !signal.aborted && asked === currentSession();
    // Library results arrive first; the catalogue can be slower or unavailable.
    if (!saved?.local)
      void api<Local>(
        "/v1/search/results?limit=20&q=" + encodeURIComponent(active),
        { signal },
      )
        .then((found) => {
          const value: Local = {
            top: found.top || [],
            songs: found.songs || [],
            albums: found.albums || [],
            artists: found.artists || [],
            playlists: found.playlists || [],
          };
          remember(key, asked, (entry) => (entry.local = value));
          if (current()) setLocal(value);
        })
        .catch((e) => {
          if (current()) setError(e.message);
        });
    return () => controller.abort();
  }, [active, session]);
  useEffect(() => {
    const wanted = settled.query;
    if (wanted.length < CATALOGUE_MINIMUM) return;
    const key = fold(wanted);
    if (answer(key)?.catalogue) return;
    const controller = new AbortController();
    const signal = controller.signal;
    const asked = currentSession();
    const current = () => !signal.aborted && asked === currentSession();
    // Shown only while it is still the query on screen.
    const showing = () => current() && fold(shown.current) === key;
    let warming: ReturnType<typeof setTimeout> | undefined;
    void api<{ artists: CatalogueArtist[] }>(
      "/v1/catalogue/artists?q=" + encodeURIComponent(wanted),
      { signal },
    )
      .then((found) => {
        remember(key, asked, (entry) => (entry.catalogue = found.artists));
        if (!showing()) return;
        setCatalogue(found.artists);
        // The likely next tap, once the query has settled: essentials for
        // two, everything for the first. A new query cancels it.
        warming = setTimeout(
          () =>
            whenIdle(() => {
              if (!showing()) return;
              found.artists
                .slice(0, 2)
                .forEach((a, i) => warmArtist(a.id, i < 1, a.name));
            }),
          WARM_AFTER_MS,
        );
      })
      .catch(() => {
        if (showing()) {
          setCatalogue([]);
          setCatalogueFailed(true);
        }
      });
    return () => {
      controller.abort();
      clearTimeout(warming);
    };
  }, [settled, session]);

  const openArtist = (event: React.MouseEvent, id: string) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return;
    event.preventDefault();
    setChoices(null);
    void api<CatalogueArtist>(`/v1/catalogue/library-artists/${id}`)
      .then((identity) => go(discoverUrl(identity)))
      .catch((e) => {
        // Several catalogue artists share the name: offer them (#251).
        if (e instanceof ApiError && e.code === "conflict") {
          const found = e.body.candidates;
          setChoices({
            detail: e.message,
            candidates: Array.isArray(found) ? (found as Candidate[]) : [],
          });
          return;
        }
        setError(e.message);
      });
  };

  // The top result is shown once, as the top result only.
  const top = local?.top[0];
  const topSong =
    top?.kind === "song" ? local?.songs.find((s) => s.id === top.id) : null;
  const topAlbum =
    top?.kind === "album" ? local?.albums.find((a) => a.id === top.id) : null;
  const topArtist =
    top?.kind === "artist" ? local?.artists.find((a) => a.id === top.id) : null;
  const topPlaylist =
    top?.kind === "playlist"
      ? local?.playlists.find((p) => p.id === top.id)
      : null;
  const songs = (local?.songs || [])
    .filter((s) => s !== topSong)
    .slice(0, SONGS_SHOWN)
    .map((t) => fromTrack(t));
  const albums = (local?.albums || [])
    .filter((a) => a !== topAlbum)
    .slice(0, ALBUMS_SHOWN);
  const serverArtists = (local?.artists || [])
    .filter((a) => a !== topArtist)
    .slice(0, ARTISTS_SHOWN);
  // A catalogue artist the server already shows is listed once, as the
  // server's; a namesake (another catalogue identity) stays, and so does
  // one whose server artist isn't on screen.
  const known = new Set(
    [topArtist, ...serverArtists]
      .map((a) => a?.musicbrainz_artist_id)
      .filter(Boolean),
  );
  const catalogueArtists = (catalogue || []).filter((a) => !known.has(a.id));
  const playlists = (local?.playlists || []).filter((p) => p !== topPlaylist);
  const artistCount = serverArtists.length + catalogueArtists.length;
  const nothing =
    local &&
    catalogue !== null &&
    !top &&
    !songs.length &&
    !albums.length &&
    !artistCount &&
    !playlists.length;

  // The top result is larger but keeps every action of its kind: the
  // collection menu, or the song row's own (a tap plays only that song).
  function topResult(): ReactNode {
    if (topAlbum)
      return (
        <CollectionCard
          kind="album"
          title={topAlbum.title}
          songs={() => albumSongs(topAlbum.id)}
        >
          <a
            className="search-top-card"
            href={"#/library/album/" + topAlbum.id}
          >
            <Cover
              title={topAlbum.title}
              src={topAlbum.cover_url}
              sizes="160px"
            />
            <strong>{topAlbum.title}</strong>
            <span className="search-top-detail">
              Album · {topAlbum.artist}
              <AlbumMark album={topAlbum} />
            </span>
          </a>
        </CollectionCard>
      );
    if (topArtist)
      return (
        <CollectionCard
          kind="artist"
          title={topArtist.name}
          songs={() => artistSongs(topArtist.id)}
        >
          <a
            className="search-top-card"
            href={"#/search?q=" + encodeURIComponent(topArtist.name)}
            onClick={(e) => openArtist(e, topArtist.id)}
          >
            <Cover
              title={topArtist.name}
              src={topArtist.cover_url}
              artist
              sizes="160px"
            />
            <strong>{topArtist.name}</strong>
            <span className="search-top-detail">Artist</span>
          </a>
        </CollectionCard>
      );
    if (topPlaylist)
      return (
        <CollectionCard
          kind="playlist"
          title={topPlaylist.name}
          songs={() => playlistSongList(topPlaylist.id)}
        >
          <a
            className="search-top-card"
            href={"#/library/playlist/" + topPlaylist.id}
          >
            <Cover title={topPlaylist.name} sizes="160px" />
            <strong>{topPlaylist.name}</strong>
            <span className="search-top-detail">
              Playlist · {plural(topPlaylist.track_count, "song")}
            </span>
          </a>
        </CollectionCard>
      );
    if (topSong) {
      const song = fromTrack(topSong);
      return (
        <div className="search-top-card search-top-song">
          <SongRow song={song} queue={[song]} index={0} page="search" />
        </div>
      );
    }
    return null;
  }

  return (
    <>
      <form
        className="search-form"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          // Enter searches at once and keeps a history entry.
          const q = clean(text);
          typed.current = false;
          setActive(q);
          if (q) {
            keepRecent(q);
            go("/search?q=" + encodeURIComponent(q));
          }
        }}
      >
        <label className="search-box">
          <Search size={22} aria-hidden="true" />
          <input
            name="q"
            type="search"
            aria-label="Search artists, albums or songs"
            placeholder="Artists, albums, songs"
            value={text}
            onChange={(e) => {
              typed.current = true;
              setText(e.target.value);
            }}
            required
            maxLength={150}
            autoFocus={!query}
            autoComplete="off"
          />
        </label>
      </form>
      {error && <InlineError>{error}</InlineError>}
      {choices && (
        <div className="artist-choice" role="group" aria-label="Choose artist">
          <p role="status">{choices.detail}</p>
          <ul className="discover-list">
            {choices.candidates.map((c) => (
              <li key={c.id}>
                <a href={"#" + discoverUrl(c)}>
                  <Cover
                    title={c.name}
                    src={catalogueArtistCover(c.id)}
                    artist
                    sizes="48px"
                  />
                  <span className="discover-text">
                    <strong>{c.name}</strong>
                    <span>
                      {["Artist", c.disambiguation].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
      {!active && (
        <BeforeTyping library={library} recent={recent} forget={forget} />
      )}
      {active && !local && !error && <Loading>Searching…</Loading>}
      {active && (
        <div
          className="search-results"
          // Opening a result keeps the search among the recent ones.
          onClickCapture={(e) => {
            if ((e.target as HTMLElement).closest("a, button"))
              keepRecent(active);
          }}
        >
          {(top || songs.length > 0) && (
            <div className="search-lead">
              {top && (
                <section
                  className="search-group search-top"
                  aria-labelledby="search-top"
                >
                  <SectionHeader id="search-top" title="Top result" />
                  {topResult()}
                </section>
              )}
              {songs.length > 0 && (
                <section
                  className="search-group search-songs"
                  aria-labelledby="search-songs"
                >
                  <SectionHeader id="search-songs" title="Songs" />
                  <div className="song-list">
                    {songs.map((song, i, all) => (
                      <SongRow
                        key={song.key}
                        song={song}
                        queue={all}
                        index={i}
                        page="search"
                      />
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}
          {albums.length > 0 && (
            <section className="search-group" aria-labelledby="search-albums">
              <SectionHeader id="search-albums" title="Albums" />
              <div className="search-album-grid">
                {albums.map((a) => (
                  <CollectionCard
                    key={a.id}
                    kind="album"
                    title={a.title}
                    songs={() => albumSongs(a.id)}
                  >
                    <a className="album-card" href={"#/library/album/" + a.id}>
                      <Cover title={a.title} src={a.cover_url} />
                      <span className="card-name search-card-name">
                        {a.title}
                        <AlbumMark album={a} />
                      </span>
                      <span className="card-caption">{a.artist}</span>
                    </a>
                  </CollectionCard>
                ))}
              </div>
            </section>
          )}
          {(artistCount > 0 || catalogue === null || catalogueFailed) && (
            <section className="search-group" aria-labelledby="search-artists">
              <SectionHeader id="search-artists" title="Artists" />
              {artistCount > 0 && (
                <ul className="discover-list">
                  {serverArtists.map((a) => (
                    <ServerArtist
                      key={a.id}
                      artist={a}
                      open={(e) => openArtist(e, a.id)}
                    />
                  ))}
                  {catalogueArtists.map((artist) => (
                    <li key={artist.id}>
                      <a href={"#" + discoverUrl(artist)}>
                        <Cover
                          title={artist.name}
                          src={catalogueArtistCover(artist.id)}
                          artist
                          sizes="48px"
                        />
                        <span className="discover-text">
                          <strong>{artist.name}</strong>
                          <span>
                            {["Artist", artist.description]
                              .filter(Boolean)
                              .join(" · ")}
                          </span>
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
              )}
              {catalogue === null ? (
                <Loading>Searching the music catalogue…</Loading>
              ) : catalogueFailed ? (
                <p className="muted">
                  The music catalogue is unavailable right now.
                </p>
              ) : null}
            </section>
          )}
          {playlists.length > 0 && (
            <section
              className="search-group"
              aria-labelledby="search-playlists"
            >
              <SectionHeader id="search-playlists" title="Playlists" />
              <ul className="discover-list">
                {playlists.map((p) => (
                  <li key={p.id}>
                    <a href={"#/library/playlist/" + p.id}>
                      <Cover title={p.name} sizes="48px" />
                      <span className="discover-text">
                        <strong>{p.name}</strong>
                        <span>Playlist · {plural(p.track_count, "song")}</span>
                      </span>
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {nothing && <EmptyLine>Nothing matches “{active}”.</EmptyLine>}
          {active.length < CATALOGUE_MINIMUM && (
            <p className="muted">
              Type another letter to search the catalogue.
            </p>
          )}
        </div>
      )}
    </>
  );
}

function ServerArtist({
  artist,
  open,
}: {
  artist: FoundArtist;
  open: (event: React.MouseEvent) => void;
}) {
  return (
    <li>
      <a href={"#/search?q=" + encodeURIComponent(artist.name)} onClick={open}>
        <Cover title={artist.name} src={artist.cover_url} artist sizes="48px" />
        <span className="discover-text">
          <strong>{artist.name}</strong>
          <span>
            {artist.in_library && artist.album_count
              ? `${plural(artist.album_count, "album")} in your library`
              : "Artist"}
          </span>
        </span>
        {artist.in_library && <StateBadge state="library" iconOnly />}
      </a>
    </li>
  );
}

type RecentAlbum = {
  id: string;
  title: string;
  artist: string;
  cover_url?: string | null;
};

/** Before typing: recent searches, your library with counts, and what was
 * added lately. */
function BeforeTyping({
  library,
  recent,
  forget,
}: {
  library: Library;
  recent: string[];
  forget: (q?: string) => void;
}) {
  const [albums, setAlbums] = useState<RecentAlbum[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    void api<{ albums: RecentAlbum[] }>("/v1/home/recent", {
      signal: controller.signal,
    })
      .then((d) => setAlbums(d.albums || []))
      .catch(() => {});
    return () => controller.abort();
  }, []);
  const shortcuts = [
    {
      id: "artists",
      label: "Artists",
      count: library.stats.artists,
      icon: <UserRound size={24} />,
    },
    {
      id: "albums",
      label: "Albums",
      count: library.stats.albums,
      icon: <Disc3 size={24} />,
    },
    {
      id: "songs",
      label: "Songs",
      count: library.stats.tracks,
      icon: <Music size={24} />,
    },
    {
      id: "playlists",
      label: "Playlists",
      count: library.playlist_count ?? 0,
      icon: <ListMusic size={24} />,
    },
  ];
  return (
    <div className="search-start">
      {recent.length > 0 && (
        <div className="search-recent" role="group" aria-label="Recent">
          <span className="search-recent-label">Recent</span>
          {recent.map((q) => (
            <span className="search-chip" key={q}>
              <a href={"#/search?q=" + encodeURIComponent(q)}>{q}</a>
              <button
                type="button"
                aria-label={`Remove ${q}`}
                onClick={() => forget(q)}
              >
                <X size={14} aria-hidden="true" />
              </button>
            </span>
          ))}
          <button
            type="button"
            className="text-button"
            onClick={() => forget()}
          >
            Clear
          </button>
        </div>
      )}
      <section className="search-group" aria-labelledby="search-yours">
        <SectionHeader id="search-yours" title="Your library" />
        <div className="search-shortcuts">
          {shortcuts.map((s) => (
            <a key={s.id} href={"#/library/" + s.id}>
              <span className="search-shortcut-icon" aria-hidden="true">
                {s.icon}
              </span>
              <span className="search-shortcut-name">{s.label}</span>
              <span className="pig-amount">{formatCount(s.count)}</span>
            </a>
          ))}
        </div>
      </section>
      {albums.length > 0 && (
        <section className="search-group" aria-labelledby="search-recent">
          <SectionHeader id="search-recent" title="Recently added" />
          <div className="search-shelf">
            {albums.slice(0, 7).map((album) => (
              <CollectionCard
                key={album.id}
                kind="album"
                title={album.title}
                songs={() => albumSongs(album.id)}
              >
                <a className="album-card" href={"#/library/album/" + album.id}>
                  <Cover title={album.title} src={album.cover_url} />
                  <span className="card-name">{album.title}</span>
                  <span className="card-caption">{album.artist}</span>
                </a>
              </CollectionCard>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
