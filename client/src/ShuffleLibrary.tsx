import { useState } from "react";
import { Shuffle } from "lucide-react";
import { api } from "./api";
import { usePlayer } from "./Player";
import { shuffled } from "./shuffle";
import type { Track } from "./types";
import { InlineError } from "./EmptyState";

type Page = { items: Track[]; total: number };
const SAMPLE_PAGES = 5;
const SAMPLE_SIZE = 200;

/** Shuffle the whole library without downloading every song's details:
 * the server pages the library, and a few random windows make the queue. */
export function ShuffleLibrary({ total }: { total: number }) {
  const player = usePlayer();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function shuffleAll() {
    setBusy(true);
    setError("");
    try {
      const starts = new Set<number>();
      const windows = Math.ceil(total / SAMPLE_SIZE);
      while (starts.size < Math.min(SAMPLE_PAGES, windows))
        starts.add(Math.floor(Math.random() * windows) * SAMPLE_SIZE);
      const pages = await Promise.all(
        [...starts].map((start) =>
          api<Page>(
            `/v1/library-pages/tracks?offset=${start}&limit=${SAMPLE_SIZE}`,
          ),
        ),
      );
      const queue = shuffled(
        pages.flatMap((p) => p.items).filter((t) => t.available !== false),
      );
      if (queue.length) player.playSaved(queue[0], queue, 0);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button
        className="secondary-button shuffle-library"
        aria-label="Shuffle all"
        disabled={!total || busy}
        onClick={() => void shuffleAll()}
      >
        <Shuffle size={18} /> <span>Shuffle all</span>
      </button>
      {error && <InlineError>{error}</InlineError>}
    </>
  );
}
