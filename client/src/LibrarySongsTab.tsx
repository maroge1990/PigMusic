import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { InlineError, Loading } from "./EmptyState";
import { formatListSize } from "./format";
import { ListActions } from "./PlaylistCards";
import { usePlayer } from "./Player";
import { shuffled } from "./shuffle";
import { fromTrack } from "./songs";
import { SortControl, byName } from "./SortControl";
import type { Track } from "./types";

type Page = { items: Track[]; total: number; next_offset: number | null };
type SongSort = "title" | "artist";
const songSorts = [
  ["title", "Title A–Z"],
  ["artist", "Artist A–Z"],
] as const;
/** The chosen sort, kept while the app stays open. */
let rememberedSort: SongSort = "title";

export function sortSongs(tracks: Track[], sort: SongSort) {
  return [...tracks].sort((a, b) =>
    sort === "artist"
      ? byName(a.artist, b.artist) ||
        byName(a.album, b.album) ||
        (a.disc_number || 0) - (b.disc_number || 0) ||
        (a.track_number || 0) - (b.track_number || 0)
      : byName(a.title, b.title) || byName(a.artist, b.artist),
  );
}

/** The Songs tab (D-120): every song in your library, read a page at a
 * time from the server, with Play, Shuffle and a sort at the top. */
export function LibrarySongsTab({
  revision,
  songs,
}: {
  /** The library's state; a change reads the songs again. */
  revision: string;
  songs: (tracks: Track[]) => ReactNode;
}) {
  const player = usePlayer();
  const [tracks, setTracks] = useState<Track[] | null>(null);
  const [error, setError] = useState("");
  const [sort, setSort] = useState<SongSort>(rememberedSort);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const all: Track[] = [];
      let offset: number | null = 0;
      while (offset !== null) {
        const page: Page = await api<Page>(
          `/v1/library-pages/tracks?offset=${offset}&limit=200`,
          { signal: controller.signal },
        );
        all.push(...page.items);
        offset = page.next_offset;
      }
      if (!controller.signal.aborted) setTracks(all);
    })().catch((e) => {
      if (!controller.signal.aborted) setError((e as Error).message);
    });
    return () => controller.abort();
  }, [revision]);
  if (error) return <InlineError>{error}</InlineError>;
  if (!tracks) return <Loading>Loading your songs…</Loading>;
  const shown = sortSongs(tracks, sort);
  const playable = shown
    .filter((t) => t.available !== false)
    .map((t) => fromTrack(t));
  const seconds = tracks.reduce((sum, t) => sum + (t.duration || 0), 0);
  return (
    <>
      <div className="songs-tab-header">
        <ListActions
          disabled={!playable.length}
          play={(shuffle) =>
            player.play(shuffle ? shuffled(playable) : playable, 0)
          }
        />
        <p>{formatListSize(tracks.length, seconds)}</p>
        <SortControl
          value={sort}
          options={songSorts}
          onChange={(value) => {
            rememberedSort = value;
            setSort(value);
          }}
        />
      </div>
      {songs(shown)}
    </>
  );
}
