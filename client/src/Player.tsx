import { api, apiFetch } from "./api";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Play, Pause, LoaderCircle } from "lucide-react";
import type { Track } from "./types";
import { useFixedHeight } from "./useFixedHeight";
import {
  flushListens,
  forgetListens,
  LISTEN_SECONDS,
  recordListen,
} from "./listens";
import { MiniPlayer } from "./MiniPlayer";
import { fromMixTrack, fromTrack, songBody, type Song } from "./songs";
import { startFailure } from "./playStart";
import { resolve, type Playing } from "./playbackSource";
import { NowPlaying, useNowPlayingFocus } from "./NowPlaying";
import {
  afterEnd,
  canGoPrevious,
  freshRadioSongs,
  hasNext as queueHasNext,
  insertNext,
  lastRadioSong,
  moveUpcoming as moveInQueue,
  nextRepeat,
  parseRepeat,
  parseSavedQueue,
  previousRestarts,
  QUEUE_KEY,
  radioRunningLow,
  removeUpcoming as removeFromQueue,
  REPEAT_KEY,
  serializeQueue,
  skipSteersRadio,
  stepIndex,
  upcomingToPrepare,
  waitsForRadio,
  type RepeatMode,
  type SavedQueue,
} from "./queue";

/** The Now Playing sheet's exit (sheet-out in Experience.css). */
const SHEET_OUT_MS = 200;

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

function readSavedQueue(): SavedQueue | null {
  try {
    return parseSavedQueue(localStorage.getItem(QUEUE_KEY));
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
  /** Moves playback and shows the new position at once. */
  function seek(seconds: number) {
    if (!audio.current) return;
    audio.current.currentTime = seconds;
    setPosition(seconds);
  }
  const [muted, setMuted] = useState(false);
  const [queueVersion, setQueueVersion] = useState(0);
  /** Where a restored queue resumes once its song loads. */
  const resumeAt = useRef(restoredQueue?.position ?? 0);
  const savedAt = useRef(0);
  /** Like iPhone, the queue and position survive a reload, restored paused. */
  function saveQueue(position?: number) {
    savedAt.current = Date.now();
    try {
      const saved = serializeQueue(
        queue.current,
        cursor.current,
        position ?? audio.current?.currentTime ?? 0,
      );
      if (saved === null) localStorage.removeItem(QUEUE_KEY);
      else localStorage.setItem(QUEUE_KEY, saved);
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

  // Closing by hand plays the sheet's exit before it goes; a stop or a
  // sign-out closes at once.
  const [leaving, setLeaving] = useState(false);
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const collapse = () => {
    if (leaveTimer.current) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches)
      return setExpanded(false);
    setLeaving(true);
    leaveTimer.current = setTimeout(() => {
      leaveTimer.current = undefined;
      setLeaving(false);
      setExpanded(false);
    }, SHEET_OUT_MS);
  };
  useEffect(() => {
    if (expanded) return;
    clearTimeout(leaveTimer.current);
    leaveTimer.current = undefined;
    setLeaving(false);
  }, [expanded]);
  useEffect(() => () => clearTimeout(leaveTimer.current), []);
  const { panel, openedFrom } = useNowPlayingFocus(expanded, (open) =>
    open ? setExpanded(true) : collapse(),
  );

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
      if (radioRunningLow(s, queue.current, cursor.current)) void refillRadio();
    } catch (e) {
      if (request === serial.current) setError((e as Error).message);
    } finally {
      if (request === serial.current) setBusy(false);
    }
  }
  /** Ask the server to fetch the next streamed songs before they are reached. */
  function prepareUpcoming() {
    const next = upcomingToPrepare(queue.current, cursor.current);
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
    const last = lastRadioSong(queue.current);
    if (!last?.cache_id) return Promise.resolve(0);
    radioRequest.current = api<{ tracks: Track[] }>(
      "/v1/radio?after=" + encodeURIComponent(last.cache_id),
    )
      .then((batch) => {
        const added = freshRadioSongs(
          queue.current,
          batch.tracks.map((t) => fromMixTrack(t, true)),
        );
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
    if (skipSteersRadio(song, playEvent.current?.seconds, LISTEN_SECONDS))
      void api("/v1/radio/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_id: song?.cache_id, action: "skip" }),
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
    insertNext(queue.current, cursor.current, s);
    setQueueVersion((v) => v + 1);
  }
  function addToQueue(s: Song) {
    if (!song) return play([s]);
    queue.current.push(s);
    setQueueVersion((v) => v + 1);
  }
  const [repeat, setRepeat] = useState<RepeatMode>(() => {
    try {
      return parseRepeat(localStorage.getItem(REPEAT_KEY));
    } catch {
      return "off";
    }
  });
  function cycleRepeat() {
    const next = nextRepeat(repeat);
    setRepeat(next);
    try {
      localStorage.setItem(REPEAT_KEY, next);
    } catch {
      /* Remembered for this visit only. */
    }
  }
  function moveUpcoming(at: number, delta: number) {
    if (!moveInQueue(queue.current, cursor.current, at, delta)) return;
    setQueueVersion((v) => v + 1);
    prepareUpcoming();
  }
  function removeUpcoming(at: number) {
    if (!removeFromQueue(queue.current, cursor.current, at)) return;
    setQueueVersion((v) => v + 1);
    prepareUpcoming();
  }
  function step(delta: number) {
    const index = stepIndex(
      queue.current.length,
      cursor.current,
      delta,
      repeat,
    );
    const next = queue.current[index];
    if (!next) {
      if (waitsForRadio(queue.current, cursor.current, delta))
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
      previousRestarts(element.currentTime, cursor.current)
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
  const canPrevious = canGoPrevious(busy, !!song?.url, cursor.current);
  const index = cursor.current;
  const hasNext = queueHasNext(queue.current, index, repeat);
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
  function toggleMute() {
    setMuted(!muted);
    if (audio.current) audio.current.muted = !muted;
  }
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
            if (afterEnd(repeat) === "again")
              void load(queue.current[cursor.current]);
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
          <MiniPlayer
            song={song}
            bar={bar}
            open={(from) => {
              openedFrom.current = from;
              setExpanded(true);
            }}
            error={error}
            playButton={playButton}
            canPrevious={canPrevious}
            hasNext={hasNext}
            busy={busy}
            previous={previous}
            next={next}
            progress={progress}
          />
        )}
        {song && expanded && (
          <NowPlaying
            song={song}
            panel={panel}
            collapse={collapse}
            leaving={leaving}
            position={position}
            duration={duration}
            seek={seek}
            playButton={playButton}
            canPrevious={canPrevious}
            hasNext={hasNext}
            busy={busy}
            previous={previous}
            next={next}
            repeat={repeat}
            cycleRepeat={cycleRepeat}
            muted={muted}
            toggleMute={toggleMute}
            close={close}
            error={error}
            songs={queue.current}
            index={index}
            step={step}
            moveUpcoming={moveUpcoming}
            removeUpcoming={removeUpcoming}
          />
        )}
      </div>
    </Context.Provider>
  );
}
