import { useEffect, useState } from "react";
import { api } from "./api";
import { Cover, catalogueCover } from "./Cover";
import { discoverUrl } from "./navigation";
import { PageHeader } from "./PageHeader";
import { SongRow } from "./SongRow";
import { withLibrary, type Song } from "./songs";
import { warmArtist, whenIdle } from "./prefetch";
import { Mixes } from "./Mixes";

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
  reason?: string;
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
const emptyMessages: Record<string, string> = {
  "recommended:no_seeds":
    "Recommendations appear once PigMusic has matched artists in your library.",
  "recommended:unavailable": "Recommendations are temporarily unavailable.",
  "popular:preparing": "Finding what\u2019s popular\u2026",
  "popular:unavailable": "Charts are temporarily unavailable.",
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
          // The chart is built in the background; keep looking for two minutes.
          if (value.state === "preparing" && attempt < 6)
            timer = setTimeout(() => void load(attempt + 1), 20000);
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
  const caption =
    feed?.source ||
    (kind === "popular"
      ? "Apple Music · Australia"
      : "Similar to artists in your library");
  const empty = feed
    ? emptyMessages[kind + ":" + (feed.state || "")] ||
      (feed.unavailable || kind === "recommended"
        ? emptyMessages[kind + ":unavailable"]
        : "")
    : "";
  return (
    <section className="home-section">
      <h2>{heading}</h2>
      {(!feed || collections.length > 0) && <p>{caption}</p>}
      {!feed && <p role="status">{error || "Finding music\u2026"}</p>}
      {feed && !collections.length && empty && <p role="status">{empty}</p>}
      {collections.map((collection) => (
        <section key={collection}>
          <h3>{collection[0].toUpperCase() + collection.slice(1)}</h3>
          {collection === "songs" ? (
            <div className="song-list home-songs">
              {(allSongs ? songs : songs.slice(0, 10)).map((song, i) => (
                <SongRow key={song.key} song={song} queue={songs} index={i} />
              ))}
              {songs.length > 10 && (
                <button type="button" onClick={() => setAllSongs(!allSongs)}>
                  {allSongs ? "Show fewer" : `Show all ${songs.length} songs`}
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
                  <p>{collection !== "artists" && item.artist}</p>
                  {item.reason && <small>{item.reason}</small>}
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
      <h2>Recently added</h2>
      <div className="home-grid">
        {albums.map((album) => (
          <a
            className="album-card"
            key={album.id}
            href={"#/library/album/" + album.id}
          >
            <Cover title={album.title} src={album.cover_url} />
            <strong>{album.title}</strong>
            <p>{album.artist}</p>
          </a>
        ))}
      </div>
    </section>
  );
}

export function Home() {
  return (
    <main className="personal-main home-page">
      <PageHeader title="Home" subtitle="Find your next favourite." />
      <Mixes />
      <RecentlyAdded />
      <HomeSection kind="recommended" />
      <HomeSection kind="popular" />
    </main>
  );
}
