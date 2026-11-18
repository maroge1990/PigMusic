import { useState } from "react";
import { Play, Shuffle } from "lucide-react";
import { currentSession } from "./api";
import { usePlayer } from "./Player";
import { shuffled } from "./shuffle";
import { fromTrack } from "./songs";
import { InlineError } from "./EmptyState";
import {
  LIBRARY_PAGE_SIZE as SAMPLE_SIZE,
  librarySource,
  readLibraryPage as libraryPage,
  type LibraryPage,
} from "./librarySource";

const SAMPLE_PAGES = 5;
const playable = (page: LibraryPage) =>
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
      // One reader for the first songs and every refill (Codex round 2).
      const read = librarySource();
      const queue = await read();
      if (currentSession() !== generation) return;
      if (queue.length) player.play(queue, 0, read);
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
