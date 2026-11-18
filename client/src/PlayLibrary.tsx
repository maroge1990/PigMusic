import { useState } from "react";
import { Play, Shuffle } from "lucide-react";
import { api, currentSession } from "./api";
import { usePlayer } from "./Player";
import { shuffled } from "./shuffle";
import type { Track } from "./types";
import { InlineError } from "./EmptyState";

type Page = { items: Track[]; total: number };
const SAMPLE_PAGES = 5;
const SAMPLE_SIZE = 200;

/** Play or shuffle the whole library without downloading every song's
 * details: the server pages the library. Play starts at the beginning;
 * Shuffle takes a few random windows. */
export function PlayLibrary({ total }: { total: number }) {
  const player = usePlayer();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function start(shuffle: boolean) {
    setBusy(true);
    setError("");
    // Songs that arrive after this session ended belong to no one (Codex).
    const generation = currentSession();
    try {
      const starts = new Set<number>(shuffle ? [] : [0]);
      const windows = Math.ceil(total / SAMPLE_SIZE);
      while (shuffle && starts.size < Math.min(SAMPLE_PAGES, windows))
        starts.add(Math.floor(Math.random() * windows) * SAMPLE_SIZE);
      const pages = await Promise.all(
        [...starts].map((start) =>
          api<Page>(
            `/v1/library-pages/tracks?offset=${start}&limit=${SAMPLE_SIZE}`,
          ),
        ),
      );
      if (currentSession() !== generation) return;
      const songs = pages
        .flatMap((p) => p.items)
        .filter((t) => t.available !== false);
      const queue = shuffle ? shuffled(songs) : songs;
      if (queue.length) player.playSaved(queue[0], queue, 0);
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
