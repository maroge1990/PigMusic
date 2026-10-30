import { api, apiFetch } from "./api";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  Repeat,
  Repeat1,
  X,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Square,
  Volume2,
  VolumeX,
  LoaderCircle,
} from "lucide-react";
import type { Track } from "./types";
import { PersonalSongActions } from "./PersonalSongActions";
import { useFixedHeight } from "./useFixedHeight";
import {
  flushListens,
  forgetListens,
  LISTEN_SECONDS,
  recordListen,
} from "./listens";
import { Cover } from "./Cover";
import {
  fromMixTrack,
  fromTrack,
  goToAlbum,
  goToPlayingArtist,
  songBody,
  type Song,
} from "./songs";
import { startFailure } from "./playStart";

type Playing = Song & {
  url?: string;
  source?: string;
  note?: string;
  /** Library track behind the audio, for play history. */
  saved_id?: string;
};
type Controls = {
  /** Play `queue[index]`, then carry on through the queue. */
  play: (queue: Song[], index?: number) => void;
  playNext: (song: Song) => void;
  addToQueue: (song: Song) => void;
  playSaved: (track: Track, queue?: Track[], position?: number) => void;
  current: string | null;
};
const Context = createContext<Controls>({
  play: () => {},
  playNext: () => {},
  addToQueue: () => {},
  playSaved: () => {},
  current: null,
});
export const usePlayer = () => useContext(Context);
const time = (n: number) =>
  `${Math.floor((n || 0) / 60)}:${String(Math.floor((n || 0) % 60)).padStart(2, "0")}`;

const QUEUE_KEY = "pig-music-queue";
function readSavedQueue(): {
  songs: Song[];
  cursor: number;
  position: number;
} | null {
  try {
    const saved = JSON.parse(localStorage.getItem(QUEUE_KEY) || "null");
    if (!Array.isArray(saved?.songs) || !saved.songs[saved.cursor]) return null;
    return {
      songs: saved.songs,
      cursor: saved.cursor,
      position: Number(saved.position) || 0,
    };
  } catch {
    return null;
  }
}

export function PlayerProvider({ children }: { children: ReactNode }) {
  const [restoredQueue] = useState(readSavedQueue);
  const audio = useRef<HTMLAudioElement>(null);
  const serial = useRef(0);
  const queue = useRef<Song[]>(restoredQueue?.songs ?? []);
  const cursor = useRef(restoredQueue?.cursor ?? -1);
  const playEvent = useRef<{
    event_id: string;
    track_id: string;
    played_at: number;
    seconds: number;
    last: number;
    recorded?: boolean;
  } | null>(null);
  /** The play ended. Its listen, if any, was already recorded at 30 seconds. */
  function reportPlay() {
    playEvent.current = null;
  }
  // A saved queue appears once someone is signed in, never on the sign-in page.
  const [song, setSong] = useState<Playing | null>(null);
  const [expanded, setExpanded] = useState(false);
  const bar = useFixedHeight("--player-height", !!song);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [muted, setMuted] = useState(false);
  const [queueVersion, setQueueVersion] = useState(0);
  /** Where a restored queue resumes once its song loads. */
  const resumeAt = useRef(restoredQueue?.position ?? 0);
  const savedAt = useRef(0);
  /** Like iPhone, the queue and position survive a reload, restored paused. */
  function saveQueue(position?: number) {
    savedAt.current = Date.now();
    try {
      if (!queue.current.length || cursor.current < 0)
        localStorage.removeItem(QUEUE_KEY);
      else
        localStorage.setItem(
          QUEUE_KEY,
          JSON.stringify({
            songs: queue.current.slice(0, 500),
            cursor: Math.min(cursor.current, 499),
            position: position ?? audio.current?.currentTime ?? 0,
          }),
        );
    } catch {
      /* Storage full or unavailable: the queue lasts for this visit. */
    }
  }

  useEffect(() => {
    const stop = () => {
      playEvent.current = null;
      try {
        localStorage.removeItem(QUEUE_KEY);
      } catch {
        /* nothing saved */
      }
      ++serial.current;
      audio.current?.pause();
      audio.current?.removeAttribute("src");
      queue.current = [];
      cursor.current = -1;
      setSong(null);
      setExpanded(false);
      setBusy(false);
      if ("mediaSession" in navigator) navigator.mediaSession.metadata = null;
    };
    const signedOut = () => {
      stop();
      forgetListens();
    };
    const signedIn = () => {
      if (restoredQueue && cursor.current === restoredQueue.cursor)
        setSong(
          (current) =>
            current ?? { ...restoredQueue.songs[restoredQueue.cursor] },
        );
    };
    window.addEventListener("music-signed-in", signedIn);
    // Whoever signs in next must never send this account's unsent listens.
    window.addEventListener("music-auth-required", signedOut);
    window.addEventListener("music-signed-out", signedOut);
    // Listens saved while offline or before the tab closed.
    void flushListens();
    const online = () => void flushListens();
    window.addEventListener("online", online);
    return () => {
      window.removeEventListener("music-signed-in", signedIn);
      window.removeEventListener("music-auth-required", signedOut);
      window.removeEventListener("music-signed-out", signedOut);
      window.removeEventListener("online", online);
    };
  }, []);

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
  const collapse = () => setExpanded(false);

  /** Resolve the audio for one song: your file, or a stream when unsaved. */
  async function resolve(s: Song) {
    if (s.cache_id) {
      const found = await resolveSaved(s.cache_id);
      return { ...found, source: "Your mix" };
    }
    // Household files play straight from the server without being added.
    if (s.state === "server" && s.server) return resolveSaved(s.server.id);
    if (s.saved) return resolveSaved(s.saved.id);
    if (s.release_id && (s.track_id || s.recording_id)) {
      const response = await apiFetch("/api/v1/remote-playback/catalogue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          release_id: s.release_id,
          track_id: s.track_id,
          recording_id: s.track_id ? undefined : s.recording_id,
        }),
      });
      const body = await response.json();
      if (!response.ok)
        throw new Error(
          typeof body.detail === "string"
            ? body.detail
            : "This song can't be streamed right now.",
        );
      return {
        url: body.url as string,
        source: body.source as string,
        note: body.note as string | undefined,
        saved_id:
          body.history_track_id ||
          (body.source === "Saved library" ? (body.id as string) : undefined),
      };
    }
    const response = await apiFetch("/api/v1/remote-playback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(songBody(s)),
    });
    const body = await response.json();
    if (!response.ok)
      throw new Error(
        typeof body.detail === "string"
          ? body.detail
          : "This song can't be streamed right now.",
      );
    return {
      url: body.url as string,
      source: body.source as string,
      note: body.note as string | undefined,
      saved_id: body.history_track_id as string | undefined,
    };
  }
  async function resolveSaved(id: string) {
    const response = await apiFetch(
      "/api/v1/playback/" + encodeURIComponent(id) + "/url",
      { method: "POST" },
    );
    const body = await response.json();
    if (!response.ok)
      throw new Error(
        typeof body.detail === "string" ? body.detail : "Playback unavailable.",
      );
    return { url: body.url as string, source: "Saved library", saved_id: id };
  }

  async function load(s: Song) {
    reportPlay();
    const request = ++serial.current;
    audio.current?.pause();
    setSong({ ...s });
    setBusy(true);
    setError("");
    setPosition(0);
    setDuration(s.duration || 0);
    try {
      const found = await resolve(s);
      if (request !== serial.current) return;
      playEvent.current = found.saved_id
        ? {
            event_id: crypto.randomUUID(),
            track_id: found.saved_id,
            played_at: Math.floor(Date.now() / 1000),
            seconds: 0,
            last: 0,
          }
        : null;
      setSong({ ...s, ...found });
      prepareUpcoming();
      if (s.radio && queue.current.length - 1 - cursor.current <= 5)
        void refillRadio();
    } catch (e) {
      if (request === serial.current) setError((e as Error).message);
    } finally {
      if (request === serial.current) setBusy(false);
    }
  }
  /** Ask the server to fetch the next streamed songs before they are reached. */
  function prepareUpcoming() {
    const next = queue.current
      .slice(cursor.current + 1, cursor.current + 3)
      .filter((s) => !s.cache_id && !s.saved && s.state !== "server");
    if (!next.length) return;
    void apiFetch("/api/v1/remote-playback/prepare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tracks: next.map(songBody) }),
    }).catch(() => undefined);
  }
  const radioRequest = useRef<Promise<number> | null>(null);
  /** Radio: append the next prepared songs; resolves to how many arrived. */
  function refillRadio(): Promise<number> {
    if (radioRequest.current) return radioRequest.current;
    const last = [...queue.current].reverse().find((s) => s.radio);
    if (!last?.cache_id) return Promise.resolve(0);
    radioRequest.current = api<{ tracks: Track[] }>(
      "/v1/radio?after=" + encodeURIComponent(last.cache_id),
    )
      .then((batch) => {
        const known = new Set(queue.current.map((s) => s.cache_id));
        const added = batch.tracks
          .map((t) => fromMixTrack(t, true))
          .filter((s) => !known.has(s.cache_id));
        queue.current.push(...added);
        if (added.length) setQueueVersion((v) => v + 1);
        return added.length;
      })
      .catch(() => 0)
      .finally(() => {
        radioRequest.current = null;
      });
    return radioRequest.current;
  }
  /** Skipping a radio song before it counts as a listen steers the station. */
  function next() {
    const event = playEvent.current;
    if (
      song?.radio &&
      song.cache_id &&
      !(event && event.seconds >= LISTEN_SECONDS)
    )
      void api("/v1/radio/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_id: song.cache_id, action: "skip" }),
      }).catch(() => undefined);
    step(1);
  }
  function play(songs: Song[], index = 0) {
    if (!songs[index]) return;
    resumeAt.current = 0;
    queue.current = songs;
    cursor.current = index;
    setQueueVersion((v) => v + 1);
    void load(songs[index]);
  }
  function playSaved(
    track: Track,
    tracks: Track[] = [track],
    position?: number,
  ) {
    const index = position ?? tracks.findIndex((t) => t.id === track.id);
    play(
      tracks.map((t) => fromTrack(t)),
      Math.max(0, index),
    );
  }
  function playNext(s: Song) {
    if (!song) return play([s]);
    queue.current.splice(cursor.current + 1, 0, s);
    setQueueVersion((v) => v + 1);
  }
  function addToQueue(s: Song) {
    if (!song) return play([s]);
    queue.current.push(s);
    setQueueVersion((v) => v + 1);
  }
  /** Like iPhone: off, the whole queue again, or the current song again. */
  const [repeat, setRepeat] = useState<"off" | "all" | "one">(() => {
    try {
      const saved = localStorage.getItem("pig-music-repeat");
      return saved === "all" || saved === "one" ? saved : "off";
    } catch {
      return "off";
    }
  });
  function cycleRepeat() {
    const next = repeat === "off" ? "all" : repeat === "all" ? "one" : "off";
    setRepeat(next);
    try {
      localStorage.setItem("pig-music-repeat", next);
    } catch {
      /* Remembered for this visit only. */
    }
  }
  function moveUpcoming(at: number, delta: number) {
    const target = at + delta;
    if (target <= cursor.current || target >= queue.current.length) return;
    const [item] = queue.current.splice(at, 1);
    queue.current.splice(target, 0, item);
    setQueueVersion((v) => v + 1);
    prepareUpcoming();
  }
  function removeUpcoming(at: number) {
    if (at <= cursor.current) return;
    queue.current.splice(at, 1);
    setQueueVersion((v) => v + 1);
    prepareUpcoming();
  }
  function step(delta: number) {
    let index = cursor.current + delta;
    // Repeat all wraps around the queue in both directions.
    if (repeat === "all" && queue.current.length)
      index = (index + queue.current.length) % queue.current.length;
    const next = queue.current[index];
    if (!next) {
      // Radio has no end: wait for the next prepared songs instead of stopping.
      if (delta > 0 && queue.current[cursor.current]?.radio)
        void refillRadio().then((added) => added && step(delta));
      return;
    }
    resumeAt.current = 0;
    cursor.current = index;
    setQueueVersion((v) => v + 1);
    void load(next);
  }
  function previous() {
    const element = audio.current;
    if (
      element &&
      song?.url &&
      (element.currentTime > 3 || cursor.current <= 0)
    ) {
      element.currentTime = 0;
      setPosition(0);
      if (playEvent.current) playEvent.current.last = 0;
    } else step(-1);
  }
  useEffect(() => {
    // A loaded song or an edited queue is saved; a restored, unloaded one keeps
    // its saved position.
    if (song?.url) saveQueue();
  }, [song?.url, queueVersion]);
  const canPrevious = !busy && (!!song?.url || cursor.current > 0);
  const index = cursor.current;
  const hasNext =
    index >= 0 &&
    (index < queue.current.length - 1 ||
      !!queue.current[index]?.radio ||
      (repeat === "all" && queue.current.length > 0));
  useEffect(() => {
    if (!song?.url) return;
    const request = serial.current;
    void audio.current?.play().catch((e) => {
      const message = startFailure(e, request !== serial.current);
      if (message) setError(message);
    });
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: song.title,
        artist: song.artist,
        album: song.album,
        artwork: song.cover
          ? [{ src: new URL(song.cover, location.href).href }]
          : [],
      });
      const handlers: { [key: string]: MediaSessionActionHandler } = {
        play: () => {
          void audio.current?.play();
        },
        pause: () => audio.current?.pause(),
        previoustrack: previous,
        nexttrack: next,
        seekto: (details) => {
          if (audio.current && details.seekTime !== undefined)
            audio.current.currentTime = details.seekTime;
        },
      };
      for (const [action, handler] of Object.entries(handlers))
        try {
          navigator.mediaSession.setActionHandler(
            action as MediaSessionAction,
            handler,
          );
        } catch {
          /* device does not support this action */
        }
    }
    return () => {
      if ("mediaSession" in navigator)
        for (const action of [
          "play",
          "pause",
          "previoustrack",
          "nexttrack",
          "seekto",
        ])
          try {
            navigator.mediaSession.setActionHandler(
              action as MediaSessionAction,
              null,
            );
          } catch {
            /* unsupported */
          }
    };
  }, [song?.url]);
  function close() {
    reportPlay();
    serial.current++;
    audio.current?.pause();
    setSong(null);
    setExpanded(false);
    setError("");
    setBusy(false);
    queue.current = [];
    cursor.current = -1;
    saveQueue();
    if ("mediaSession" in navigator) navigator.mediaSession.metadata = null;
  }
  function toggle() {
    if (!audio.current) return;
    if (playing) audio.current.pause();
    else
      void audio.current
        .play()
        .then(() => setError(""))
        .catch(() =>
          setError(
            "Audio could not start. Try another browser or download this song.",
          ),
        );
  }
  const playButton = (size: number) => (
    <button
      className="np-play"
      aria-label={playing ? "Pause" : "Play"}
      disabled={busy || !song}
      onClick={() =>
        // A restored song loads on the first Play, at its saved position.
        song?.url ? toggle() : song && void load(queue.current[cursor.current])
      }
    >
      {busy ? (
        <LoaderCircle size={size} className="spin" />
      ) : playing ? (
        <Pause size={size} fill="currentColor" />
      ) : (
        <Play size={size} fill="currentColor" />
      )}
    </button>
  );
  const progress = duration ? Math.min(1, position / duration) : 0;
  return (
    <Context.Provider
      value={{
        play,
        playNext,
        addToQueue,
        playSaved,
        current: song?.key || null,
      }}
    >
      <div className={song ? "media-shell has-player" : "media-shell"}>
        {children}
        <audio
          ref={audio}
          src={song?.url}
          preload="metadata"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onTimeUpdate={() => {
            const current = audio.current?.currentTime || 0;
            const event = playEvent.current;
            if (event) {
              const elapsed = current - event.last;
              if (playing && elapsed > 0 && elapsed < 3)
                event.seconds += elapsed;
              event.last = current;
              // Like iPhone: one listen per play, once 30 seconds were heard,
              // kept in the browser until the server confirms it.
              if (!event.recorded && event.seconds >= LISTEN_SECONDS) {
                event.recorded = true;
                recordListen({
                  event_id: event.event_id,
                  track_id: event.track_id,
                  played_at: event.played_at,
                  seconds: LISTEN_SECONDS,
                });
              }
            }
            setPosition(current);
            if (Date.now() - savedAt.current > 5000) saveQueue(current);
          }}
          onLoadedMetadata={() => {
            if (resumeAt.current > 0 && audio.current) {
              audio.current.currentTime = resumeAt.current;
              resumeAt.current = 0;
            }
          }}
          onDurationChange={() => {
            const n = audio.current?.duration;
            // A VBR MP3 without a length header can report about double its
            // real length; the song's known length wins unless the two agree.
            const known = song?.duration || 0;
            if (!n || !Number.isFinite(n)) return;
            setDuration(
              !known || Math.abs(n - known) <= Math.max(5, known * 0.05)
                ? n
                : known,
            );
          }}
          onEnded={() => {
            reportPlay();
            setPlaying(false);
            // Repeat one plays the song again as a new play.
            if (repeat === "one") void load(queue.current[cursor.current]);
            else step(1);
          }}
          onError={() => {
            if (song?.url)
              setError(
                "This audio stream could not be played. The provider or format may be unavailable.",
              );
          }}
        />
        {song && (
          <section ref={bar} className="mini-player" aria-label="Now playing">
            <button
              className="mini-open"
              aria-label={`Open now playing: ${song.title}`}
              onClick={(e) => {
                openedFrom.current = e.currentTarget;
                setExpanded(true);
              }}
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
        )}
        {song && expanded && (
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
                step="0.1"
                value={Math.min(position, duration || 1)}
                disabled={!song.url}
                onChange={(e) => {
                  if (audio.current)
                    audio.current.currentTime = Number(e.target.value);
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
              <button
                aria-label={muted ? "Unmute" : "Mute"}
                onClick={() => {
                  setMuted(!muted);
                  if (audio.current) audio.current.muted = !muted;
                }}
              >
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
            {queue.current.length > index + 1 && (
              <section className="np-queue" aria-label="Up next">
                <h3>Up next</h3>
                <ol>
                  {queue.current.slice(index + 1, index + 21).map((s, i) => {
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
                            disabled={at === queue.current.length - 1}
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
                {queue.current.length - index - 1 > 20 && (
                  <p className="np-queue-more">
                    and {queue.current.length - index - 21} more
                  </p>
                )}
              </section>
            )}
          </section>
        )}
      </div>
    </Context.Provider>
  );
}
