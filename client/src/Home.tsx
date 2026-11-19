import { useEffect, useState } from "react";
import { api } from "./api";
import { Cover, catalogueCover } from "./Cover";
import { discoverUrl } from "./navigation";
import { PageHeader, SectionHeader } from "./PageHeader";
import { SongRow } from "./SongRow";
import { withLibrary, type Song } from "./songs";
import { warmArtist, whenIdle } from "./prefetch";
import { MixPage, Mixes } from "./Mixes";
import { CollectionCard, albumSongs } from "./CollectionMenu";
import {
  CoverPlay,
  ShelfLoading,
  ShelfPlaceholder,
  usePlaceholder,
} from "./HomeShelf";
import { Rewind, RewindCard, rewindPeriod } from "./Rewind";
import type { Route } from "./routes";

function songRows(items: Item[]): Song[] {
  return items.map((item) => ({
    key: "home:" + item.id,
    title: item.name,
    artist: item.artist,
    album: item.album || undefined,
    cover: item.release_id ? catalogueCover("release", item.release_id) : null,
    state: "missing",
    release_id: item.release_id,
    recording_id: item.id,
    artist_id: item.artist_id,
  }));
}
type Item = {
  id: string;
  name: string;
  artist: string;
  artist_id: string;
  release_id?: string;
  album?: string;
  kind: string;
};
type Feed = {
  artists: Item[];
  albums: Item[];
  songs: Item[];
  source: string;
  title?: string;
  state?: "ready" | "no_seeds" | "unavailable" | "preparing";
  unavailable: boolean;
};
function HomeSection({ kind }: { kind: "popular" | "recommended" }) {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [songs, setSongs] = useState<Song[]>([]);
  // The chart holds up to 100 songs (#55): the first ten, then all on request.
  const [allSongs, setAllSongs] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = (attempt: number) =>
      api<Feed>("/v1/home/" + kind, { signal: controller.signal })
        .then((value) => {
          setFeed(value);
          // The chart is built in the background, and a first-ever build can
          // take several minutes (#105): look every 20 s for two minutes,
          // then every minute for as long as Home stays open.
          if (value.state === "preparing")
            timer = setTimeout(
              () => void load(attempt + 1),
              attempt < 6 ? 20000 : 60000,
            );
          // Every artist here opens instantly; the first two are fully ready.
          whenIdle(() =>
            [
              ...new Map(
                [
                  ...value.artists,
                  ...value.albums,
                  ...(kind === "recommended" ? [] : value.songs),
                ].map((item) => [item.artist_id, item.artist] as const),
              ),
            ]
              .slice(0, 12)
              .forEach(([id, name], i) => warmArtist(id, i < 2, name)),
          );
          if (kind === "recommended") return;
          const plain = songRows(value.songs);
          setSongs(plain);
          return withLibrary(plain).then((found) => {
            if (!controller.signal.aborted) setSongs(found);
          });
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message);
        });
    void load(0);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [kind]);
  // Recommendations are artists and albums only; songs stay in the charts.
  const collections = (
    kind === "recommended"
      ? (["artists", "albums"] as const)
      : (["artists", "albums", "songs"] as const)
  ).filter((collection) => feed?.[collection].length);
  const heading =
    kind === "popular"
      ? feed?.title || "Popular in Australia"
      : "Recommended for you";
  // Only the chart names its source; recommendations need no caption (D-101).
  const caption =
    kind === "popular" ? feed?.source || "Apple Music · Australia" : undefined;
  // Shown only with something in it; while the first answer or the chart's
  // first build is on its way, at most a quiet placeholder row (D-120).
  const placeholder = usePlaceholder(
    kind,
    !collections.length && !error && (!feed || feed.state === "preparing"),
  );
  if (!collections.length)
    return placeholder ? <ShelfPlaceholder label={heading} /> : null;
  return (
    <section className="home-section">
      <SectionHeader title={heading} subtitle={caption} />
      {collections.map((collection) => (
        <section key={collection}>
          <h3>{collection[0].toUpperCase() + collection.slice(1)}</h3>
          {collection === "songs" ? (
            <div className="song-list home-songs">
              {(allSongs ? songs : songs.slice(0, 10)).map((song, i) => (
                <SongRow
                  key={song.key}
                  song={song}
                  queue={songs}
                  index={i}
                  page="chart"
                />
              ))}
              {songs.length > 10 && (
                <button type="button" onClick={() => setAllSongs(!allSongs)}>
                  {allSongs ? "Show fewer" : "See all popular songs"}
                </button>
              )}
            </div>
          ) : (
            <div className="home-grid">
              {feed![collection].map((item) => (
                <button
                  className="album-card"
                  key={item.id}
                  onClick={() => {
                    location.hash =
                      discoverUrl({
                        id: item.artist_id,
                        name: item.artist || item.name,
                      }) + (collection === "albums" ? "&group=" + item.id : "");
                  }}
                >
                  <Cover
                    title={item.name}
                    artist={collection === "artists"}
                    src={
                      collection === "artists"
                        ? `/api/v1/catalogue/artists/${item.id}/artwork`
                        : catalogueCover("release-group", item.id)
                    }
                  />
                  <strong>{item.name}</strong>
                  <span className="card-caption">
                    {collection !== "artists" && item.artist}
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>
      ))}
      {feed && collections.length > 0 && feed.unavailable && (
        <p role="status">
          Some discovery feeds are unavailable. Your library is still available.
        </p>
      )}
    </section>
  );
}
type RecentAlbum = {
  id: string;
  title: string;
  artist: string;
  cover_url?: string | null;
};

/** Music this person added lately; hidden until there is some. */
function RecentlyAdded() {
  const [albums, setAlbums] = useState<RecentAlbum[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    void api<{ albums: RecentAlbum[] }>("/v1/home/recent", {
      signal: controller.signal,
    })
      .then((d) => setAlbums(d.albums))
      .catch(() => {});
    return () => controller.abort();
  }, []);
  if (!albums.length) return null;
  return (
    <section className="home-section">
      <SectionHeader title="Recently added" />
      <div className="home-grid">
        {albums.map((album) => (
          <CollectionCard
            key={album.id}
            kind="album"
            title={album.title}
            songs={() => albumSongs(album.id)}
          >
            <a className="album-card" href={"#/library/album/" + album.id}>
              <Cover title={album.title} src={album.cover_url} />
              <strong>{album.title}</strong>
              <p>{album.artist}</p>
            </a>
            <CoverPlay title={album.title} songs={() => albumSongs(album.id)} />
          </CollectionCard>
        ))}
      </div>
    </section>
  );
}

export function Home({ route }: { route: Route }) {
  if (route.parts[0] === "mix" && route.parts[1])
    return <MixPage id={decodeURIComponent(route.parts[1])} />;
  if (route.parts[0] === "rewind")
    return <Rewind period={rewindPeriod(route.params.get("period"))} />;
  return (
    <main className="personal-main home-page">
      <PageHeader title="Home" />
      <RewindCard />
      <ShelfLoading>
        <Mixes />
        <RecentlyAdded />
        <HomeSection kind="recommended" />
        <HomeSection kind="popular" />
      </ShelfLoading>
    </main>
  );
}
