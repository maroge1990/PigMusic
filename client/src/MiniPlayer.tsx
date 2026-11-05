import type { ReactNode, RefObject } from "react";
import { SkipBack, SkipForward } from "lucide-react";
import { Cover } from "./Cover";
import type { Playing } from "./playbackSource";

/** The bar along the bottom: the song, previous, play and next. */
export function MiniPlayer({
  song,
  bar,
  open,
  error,
  playButton,
  canPrevious,
  hasNext,
  busy,
  previous,
  next,
  progress,
}: {
  song: Playing;
  bar: RefObject<HTMLElement | null>;
  /** Opens Now Playing; focus returns to `from` when it closes. */
  open: (from: HTMLElement) => void;
  error: string;
  playButton: (size: number) => ReactNode;
  canPrevious: boolean;
  hasNext: boolean;
  busy: boolean;
  previous: () => void;
  next: () => void;
  progress: number;
}) {
  return (
    <section ref={bar} className="mini-player" aria-label="Now playing">
      <button
        className="mini-open"
        aria-label={`Open now playing: ${song.title}`}
        onClick={(e) => open(e.currentTarget)}
      >
        <Cover title={song.title} src={song.cover} sizes="48px" />
        <span>
          <strong>{song.title}</strong>
          <small>
            {/* No status text in normal use (#60): the play button's
                spinner shows loading; only a problem gets words. */}
            {error
              ? error
              : song.source && song.source !== "Saved library"
                ? `${song.artist} · Streaming`
                : song.artist}
          </small>
        </span>
      </button>
      <button
        className="mini-next"
        aria-label="Previous song"
        disabled={!canPrevious}
        onClick={previous}
      >
        <SkipBack size={22} fill="currentColor" />
      </button>
      {playButton(22)}
      <button
        className="mini-next"
        aria-label="Next song"
        disabled={!hasNext || busy}
        onClick={next}
      >
        <SkipForward size={22} fill="currentColor" />
      </button>
      <span
        className="mini-progress"
        aria-hidden="true"
        style={{ transform: `scaleX(${progress})` }}
      />
    </section>
  );
}
