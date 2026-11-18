import { useEffect, useRef, useState } from "react";
import { api, currentSession } from "./api";
import { Loading } from "./EmptyState";
import { formatListSize } from "./format";
import { dismiss, notifyError } from "./notices";
import { PlayLibrary } from "./PlayLibrary";
import { SongRow } from "./SongRow";
import { fromTrack, type Song } from "./songs";
import type { Track } from "./types";

type Page = {
  items: Track[];
  total: number;
  next_offset: number | null;
  revision: string;
};
const PAGE_SIZE = 200;

/** The Songs tab (D-120): your songs in the server's order, the first page
 * at once and each next one as you scroll near the end (Codex). Every page
 * must come from the same library state as the first; if the library
 * changed in between, the list starts again from the top. A page that
 * fails says so with Try again for that page. The server can't sort its
 * pages, so the tab has no Sort yet. */
export function LibrarySongsTab({
  revision,
  total,
  onRemoved,
}: {
  /** The library's state; a change reads the songs again. */
  revision: string;
  total: number;
  /** After a song leaves your library. */
  onRemoved?: () => void;
}) {
  const [songs, setSongs] = useState<Song[] | null>(null);
  const [count, setCount] = useState(total);
  const state = useRef({
    next: 0 as number | null,
    revision: "",
    loading: false,
    notice: 0,
    run: 0,
  });
  const end = useRef<HTMLDivElement>(null);

  async function load() {
    const s = state.current;
    if (s.loading || s.next === null) return;
    s.loading = true;
    const run = s.run;
    const generation = currentSession();
    const offset = s.next;
    try {
      const page = await api<Page>(
        `/v1/library-pages/tracks?offset=${offset}&limit=${PAGE_SIZE}`,
      );
      if (run !== s.run || generation !== currentSession()) return;
      if (s.notice) dismiss(s.notice);
      s.notice = 0;
      if (offset > 0 && page.revision !== s.revision) {
        // The library changed under the list: start again from the top.
        restart();
        return;
      }
      s.revision = page.revision;
      s.next = page.next_offset;
      setCount(page.total);
      const added = page.items.map((t) => fromTrack(t));
      setSongs((old) => (offset === 0 ? added : [...(old || []), ...added]));
    } catch (e) {
      if (run !== s.run || generation !== currentSession()) return;
      s.notice = notifyError(
        (e as Error).message,
        () => {
          s.notice = 0;
          return load();
        },
        "library-songs-page",
      );
    } finally {
      if (run === s.run) s.loading = false;
    }
  }
  function restart() {
    const s = state.current;
    s.run++;
    s.next = 0;
    s.revision = "";
    s.loading = false;
    void load();
  }
  useEffect(() => {
    restart();
    return () => {
      state.current.run++;
      if (state.current.notice) dismiss(state.current.notice);
    };
  }, [revision]);
  // The next page is read as the end of the list comes into view.
  useEffect(() => {
    const target = end.current;
    if (!target || !songs) return;
    const watcher = new IntersectionObserver(
      (seen) => {
        if (seen.some((e) => e.isIntersecting)) void load();
      },
      { rootMargin: "800px 0px" },
    );
    watcher.observe(target);
    return () => watcher.disconnect();
  }, [songs]);
  if (!songs) return <Loading>Loading your songs…</Loading>;
  return (
    <>
      <div className="songs-tab-header">
        <PlayLibrary total={count} />
        <p>{formatListSize(count)}</p>
      </div>
      <div className="song-list">
        {songs.map((song, index) => (
          <SongRow
            key={song.key}
            song={song}
            queue={songs}
            index={index}
            page="library"
            onRemoved={onRemoved}
          />
        ))}
      </div>
      <div ref={end} className="songs-tab-end" aria-hidden="true" />
    </>
  );
}
