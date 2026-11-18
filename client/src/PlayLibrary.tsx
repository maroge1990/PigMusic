import { useState } from "react";
import { Play, Shuffle } from "lucide-react";
import { api, currentSession } from "./api";
import { usePlayer } from "./Player";
import { shuffled } from "./shuffle";
import { fromTrack } from "./songs";
import type { Track } from "./types";
import { InlineError } from "./EmptyState";

type Page = { items: Track[]; total: number; next_offset?: number | null };
const SAMPLE_PAGES = 5;
const SAMPLE_SIZE = 200;

const libraryPage = (offset: number) =>
  api<Page>(`/v1/library-pages/tracks?offset=${offset}&limit=${SAMPLE_SIZE}`);
const playable = (page: Page) =>
  page.items.filter((t) => t.available !== false).map((t) => fromTrack(t));

/** Play or shuffle the whole library without downloading every song's
 * details: the server pages the library. Play starts at the beginning and
 * reads each next page before the queue runs out; Shuffle takes a few
 * random windows. What arrives after the session ended is dropped (Codex). */
export function PlayLibrary({ total }: { total: number }) {
  const player = usePlayer();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function start(shuffle: boolean) {
    setBusy(true);
    setError("");
    const generation = currentSession();
    try {
      if (shuffle) {
        const starts = new Set<number>();
        const windows = Math.ceil(total / SAMPLE_SIZE);
        while (starts.size < Math.min(SAMPLE_PAGES, windows))
          starts.add(Math.floor(Math.random() * windows) * SAMPLE_SIZE);
        const pages = await Promise.all([...starts].map(libraryPage));
        if (currentSession() !== generation) return;
        const queue = shuffled(pages.flatMap(playable));
        if (queue.length) player.play(queue, 0);
        return;
      }
      const first = await libraryPage(0);
      if (currentSession() !== generation) return;
      let next = first.next_offset ?? null;
      const more = async () => {
        // Pages with nothing playable are passed over, up to the end.
        while (next !== null) {
          const page = await libraryPage(next);
          next = page.next_offset ?? null;
          const songs = playable(page);
          if (songs.length) return songs;
        }
        return [];
      };
      const queue = playable(first);
      if (queue.length) player.play(queue, 0, more);
    } catch (e) {
      if (currentSession() === generation) setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button
        className="primary-button library-play"
        aria-label="Play"
        disabled={!total || busy}
        onClick={() => void start(false)}
      >
        <Play size={16} fill="currentColor" aria-hidden="true" />{" "}
        <span>Play</span>
      </button>
      <button
        className="secondary-button library-play"
        aria-label="Shuffle"
        disabled={!total || busy}
        onClick={() => void start(true)}
      >
        <Shuffle size={16} aria-hidden="true" /> <span>Shuffle</span>
      </button>
      {error && <InlineError>{error}</InlineError>}
    </>
  );
}
