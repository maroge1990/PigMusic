import type { ReactNode, RefObject } from "react";
import { useEffect, useRef } from "react";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  Repeat,
  Repeat1,
  X,
  SkipBack,
  SkipForward,
  Square,
  Volume2,
  VolumeX,
} from "lucide-react";
import { Cover } from "./Cover";
import { PersonalSongActions } from "./PersonalSongActions";
import type { Playing } from "./playbackSource";
import type { RepeatMode } from "./queue";
import { goToAlbum, goToPlayingArtist, type Song } from "./songs";

const time = (n: number) =>
  `${Math.floor((n || 0) / 60)}:${String(Math.floor((n || 0) % 60)).padStart(2, "0")}`;

/** Now Playing is a modal: it keeps keyboard focus inside while open and
 * hands it back to what opened it on close. */
export function useNowPlayingFocus(
  expanded: boolean,
  setExpanded: (open: boolean) => void,
) {
  const panel = useRef<HTMLElement>(null);
  /** What opened Now Playing, to hand focus back to once it closes. */
  const openedFrom = useRef<HTMLElement | null>(null);
  useEffect(() => {
    document.documentElement.classList.toggle("now-playing-open", expanded);
    if (!expanded) {
      // The mini player is visible again only now, so focus can return to it.
      const trigger = openedFrom.current;
      openedFrom.current = null;
      if (trigger?.isConnected) trigger.focus();
      return;
    }
    // A modal keeps keyboard focus inside it and hands it back on close.
    const keydown = (e: KeyboardEvent) => {
      if (e.key === "Escape") return setExpanded(false);
      if (e.key !== "Tab" || !panel.current) return;
      const focusable = Array.from(
        panel.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const inside = panel.current.contains(document.activeElement);
      if (e.shiftKey && (document.activeElement === first || !inside)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !inside)) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [expanded]);
  return { panel, openedFrom };
}

/** The full-screen player: song, seek bar, transport, actions and Up next. */
export function NowPlaying({
  song,
  panel,
  collapse,
  position,
  duration,
  seek,
  playButton,
  canPrevious,
  hasNext,
  busy,
  previous,
  next,
  repeat,
  cycleRepeat,
  muted,
  toggleMute,
  close,
  error,
  songs,
  index,
  step,
  moveUpcoming,
  removeUpcoming,
}: {
  song: Playing;
  panel: RefObject<HTMLElement | null>;
  collapse: () => void;
  position: number;
  duration: number;
  seek: (seconds: number) => void;
  playButton: (size: number) => ReactNode;
  canPrevious: boolean;
  hasNext: boolean;
  busy: boolean;
  previous: () => void;
  next: () => void;
  repeat: RepeatMode;
  cycleRepeat: () => void;
  muted: boolean;
  toggleMute: () => void;
  close: () => void;
  error: string;
  songs: Song[];
  index: number;
  step: (delta: number) => void;
  moveUpcoming: (at: number, delta: number) => void;
  removeUpcoming: (at: number) => void;
}) {
  return (
    <section
      ref={panel}
      className="now-playing"
      role="dialog"
      aria-modal="true"
      aria-label="Now playing"
    >
      <div className="now-playing-backdrop" aria-hidden="true">
        {song.cover && (
          <Cover title={song.title} src={song.cover} sizes="128px" />
        )}
      </div>
      <button
        className="np-collapse"
        aria-label="Close now playing"
        autoFocus
        onClick={collapse}
      >
        <ChevronDown size={28} />
      </button>
      <Cover
        className="np-cover"
        title={song.title}
        src={song.cover}
        sizes="(max-width: 600px) 90vw, 420px"
      />
      <div className="np-meta">
        <h2>{song.title}</h2>
        <p>
          <button
            className="music-text-link"
            onClick={() => {
              collapse();
              void goToPlayingArtist(song).catch(() => {});
            }}
          >
            {song.artist}
          </button>
          {song.album && (
            <>
              {" · "}
              <button
                className="music-text-link"
                onClick={() => {
                  collapse();
                  void goToAlbum(
                    song.album!,
                    song.album_artist || song.artist,
                    song.release_id,
                    song.artist_id,
                  ).catch(() => {});
                }}
              >
                {song.album}
              </button>
            </>
          )}
        </p>
        {song.note && <small>{song.note}</small>}
      </div>
      <div className="np-seek">
        <input
          aria-label="Playback position"
          type="range"
          min="0"
          max={duration || 1}
          step="1"
          value={Math.min(position, duration || 1)}
          aria-valuetext={`${time(position)} of ${time(duration)}`}
          disabled={!song.url}
          onChange={(e) => seek(Number(e.target.value))}
          onKeyDown={(e) => {
            // Arrow keys move five seconds, like most players (#97).
            const by = {
              ArrowRight: 5,
              ArrowUp: 5,
              ArrowLeft: -5,
              ArrowDown: -5,
            }[e.key];
            if (!by) return;
            e.preventDefault();
            seek(Math.max(0, Math.min(duration || 0, position + by)));
          }}
        />
        <span>{time(position)}</span>
        <span>-{time(Math.max(0, duration - position))}</span>
      </div>
      <div className="np-transport">
        <button
          aria-label="Previous song"
          disabled={!canPrevious}
          onClick={previous}
        >
          <SkipBack size={30} fill="currentColor" />
        </button>
        {playButton(34)}
        <button
          aria-label="Next song"
          disabled={!hasNext || busy}
          onClick={next}
        >
          <SkipForward size={30} fill="currentColor" />
        </button>
      </div>
      <div className="np-actions">
        <PersonalSongActions song={song} />
        <button
          aria-label={
            repeat === "off"
              ? "Repeat off"
              : repeat === "all"
                ? "Repeat all"
                : "Repeat this song"
          }
          aria-pressed={repeat !== "off"}
          onClick={cycleRepeat}
        >
          {repeat === "one" ? (
            <Repeat1 size={20} />
          ) : (
            <Repeat size={20} opacity={repeat === "off" ? 0.45 : 1} />
          )}
        </button>
        <button aria-label={muted ? "Unmute" : "Mute"} onClick={toggleMute}>
          {muted ? <VolumeX size={20} /> : <Volume2 size={20} />}
        </button>
        <button aria-label="Stop and close player" onClick={close}>
          <Square size={18} />
        </button>
      </div>
      {error && (
        <p className="np-error" role="alert">
          {error}
        </p>
      )}
      <UpNext
        songs={songs}
        index={index}
        step={step}
        move={moveUpcoming}
        remove={removeUpcoming}
      />
    </section>
  );
}

/** The next twenty songs, each playable, movable and removable. */
function UpNext({
  songs,
  index,
  step,
  move: moveUpcoming,
  remove: removeUpcoming,
}: {
  songs: Song[];
  index: number;
  step: (delta: number) => void;
  move: (at: number, delta: number) => void;
  remove: (at: number) => void;
}) {
  if (songs.length <= index + 1) return null;
  return (
    <section className="np-queue" aria-label="Up next">
      <h3>Up next</h3>
      <ol>
        {songs.slice(index + 1, index + 21).map((s, i) => {
          const at = index + 1 + i;
          return (
            <li key={s.key + at}>
              <button onClick={() => step(i + 1)}>
                <strong>{s.title}</strong>
                <small>{s.artist}</small>
              </button>
              <span className="np-queue-tools">
                <button
                  aria-label={`Move ${s.title} up`}
                  disabled={i === 0}
                  onClick={() => moveUpcoming(at, -1)}
                >
                  <ArrowUp size={16} />
                </button>
                <button
                  aria-label={`Move ${s.title} down`}
                  disabled={at === songs.length - 1}
                  onClick={() => moveUpcoming(at, 1)}
                >
                  <ArrowDown size={16} />
                </button>
                <button
                  aria-label={`Remove ${s.title} from Up next`}
                  onClick={() => removeUpcoming(at)}
                >
                  <X size={16} />
                </button>
              </span>
            </li>
          );
        })}
      </ol>
      {songs.length - index - 1 > 20 && (
        <p className="np-queue-more">and {songs.length - index - 21} more</p>
      )}
    </section>
  );
}
