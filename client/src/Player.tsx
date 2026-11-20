import { api, apiFetch, currentSession } from "./api";
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
import { notifyError } from "./notices";
import {
  fromMixTrack,
  fromSimilarTrack,
  fromTrack,
  songBody,
  type Song,
} from "./songs";
import { keepPlaying } from "./keepPlaying";
import { startFailure } from "./playStart";
import { StartClock, startSource, type StartAction } from "./firstSound";
import { resolve, type Playing } from "./playbackSource";
import { NowPlaying, useNowPlayingFocus } from "./NowPlaying";
import {
  afterEnd,
  canGoPrevious,
  continuesWithSimilar,
  freshRadioSongs,
  freshSimilarSongs,
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
  serverSongId,
  similarRequest,
  skipSteersRadio,
  stepIndex,
  upcomingToPrepare,
  waitsForRadio,
  type RepeatMode,
  type SavedQueue,
} from "./queue";

/** The songs after a queue's end, read a page at a time; an empty answer
 * means there are no more. */
export type MoreSongs = () => Promise<Song[]>;
/** A continued queue reads its next page once this few songs are left. */
const MORE_LOW_WATER = 20;

/** The Now Playing sheet's exit (sheet-out in Experience.css). */
const SHEET_OUT_MS = 200;

type Controls = {
  /** Play `queue[index]`, then carry on through the queue. `more`, when
   * given, reads the songs after the queue's end; it is asked before the
   * queue runs out, until it answers none. */
  play: (queue: Song[], index?: number, more?: MoreSongs) => void;
  /** One song, or a whole album or playlist in its order. */
  playNext: (song: Song | Song[]) => void;
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
  const startClock = useRef(new StartClock());
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
      // A refill or continuation still on its way belongs to the old queue.
      radioRequest.current = null;
      moreSource.current = null;
      similarRequestRef.current = null;
      setSong(null);
      setExpanded(false);
      setBusy(false);
      if ("mediaSession" in navigator) navigator.mediaSession.metadata = null;
    };
    // Signed out or expired: the session's own end (below) forgets its
    // listens; the player stops at once either way.
    const signedOut = () => stop();
    const sessionEnded = (event: Event) => {
      stop();
      forgetListens((event as CustomEvent<string | null>).detail ?? null);
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
    // Every end of a session (expiry, sign-out, another tab's sign-in or
    // sign-out) clears the shared reads; the player ends with it, before
    // the next account is read (Codex).
    window.addEventListener("music-session-cleared", sessionEnded);
    // Listens saved while offline or before the tab closed.
    void flushListens();
    const online = () => void flushListens();
    window.addEventListener("online", online);
    return () => {
      window.removeEventListener("music-signed-in", signedIn);
      window.removeEventListener("music-auth-required", signedOut);
      window.removeEventListener("music-signed-out", signedOut);
      window.removeEventListener("music-session-cleared", sessionEnded);
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

  async function load(s: Song, action: StartAction = "play") {
    reportPlay();
    const request = ++serial.current;
    startClock.current.begin(request, action, performance.now());
    audio.current?.pause();
    setSong({ ...s });
    setBusy(true);
    setError("");
    setPosition(0);
    setDuration(s.duration || 0);
    // Like iPhone: as the last song starts, similar songs join Up next, so the
    // queue plays on as it does between songs instead of asking only once the
    // music has stopped (#202).
    // A queue with more pages to come reads the next one before it runs out;
    // similar songs wait until it has no more.
    if (moreFor(queue.current)) {
      if (queue.current.length - 1 - cursor.current <= MORE_LOW_WATER)
        void readMore();
    } else if (
      continuesWithSimilar(
        queue.current,
        cursor.current,
        1,
        repeat,
        keepPlaying(),
      )
    )
      void continueWithSimilar();
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
      startClock.current.cancel(request);
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
  /** A request still on its way, with the queue and session it is for. */
  type Pending = {
    request: Promise<number>;
    songs: Song[];
    generation: number;
  };
  /** Only a request for this very queue, in this session, is joined
   * (Codex): a new queue starts its own. */
  const joinable = (pending: Pending | null) =>
    pending &&
    pending.songs === queue.current &&
    pending.generation === currentSession()
      ? pending.request
      : null;
  /** Where a continued queue's next songs come from (#251, Codex). */
  const moreSource = useRef<{
    songs: Song[];
    generation: number;
    next: MoreSongs;
    pending: Promise<number> | null;
  } | null>(null);
  /** The source still feeding this very queue in this session, if any. */
  const moreFor = (songs: Song[]) => {
    const source = moreSource.current;
    return source &&
      source.songs === songs &&
      source.generation === currentSession()
      ? source
      : null;
  };
  /** Append the next page; resolves to how many songs arrived. */
  function readMore(): Promise<number> {
    const source = moreFor(queue.current);
    if (!source) return Promise.resolve(0);
    if (source.pending) return source.pending;
    const request = source
      .next()
      .then((more) => {
        // A new queue, a stop or another account in the meantime: these
        // songs belong to no one.
        if (moreFor(source.songs) !== source || queue.current !== source.songs)
          return 0;
        if (!more.length) moreSource.current = null;
        source.songs.push(...more);
        setQueueVersion((v) => v + 1);
        return more.length;
      })
      .catch((e: Error) => {
        // The source stays: Next is still offered, and Try again reads the
        // same page again (Codex round 2).
        if (moreFor(source.songs) === source)
          notifyError(e.message, () => readMore(), "player-more-songs");
        return 0;
      })
      .finally(() => {
        if (source.pending === request) source.pending = null;
      });
    source.pending = request;
    return request;
  }
  const radioRequest = useRef<Pending | null>(null);
  /** Radio: append the next prepared songs; resolves to how many arrived. */
  function refillRadio(): Promise<number> {
    const joined = joinable(radioRequest.current);
    if (joined) return joined;
    const last = lastRadioSong(queue.current);
    if (!last?.cache_id) return Promise.resolve(0);
    // The refill belongs to this queue in this session (Codex): a stop, a
    // new queue or another account in the meantime discards it.
    const songs = queue.current;
    const generation = currentSession();
    // Its own HTTP request (#271): an identical refill from an abandoned
    // queue may stall or fail, and must not hold this queue's Next.
    const request: Promise<number> = api<{ tracks: Track[] }>(
      "/v1/radio?after=" + encodeURIComponent(last.cache_id),
      { unshared: true },
    )
      .then((batch) => {
        if (queue.current !== songs || currentSession() !== generation)
          return 0;
        const added = freshRadioSongs(
          songs,
          batch.tracks.map((t) => fromMixTrack(t, true)),
        );
        songs.push(...added);
        if (added.length) setQueueVersion((v) => v + 1);
        return added.length;
      })
      .catch(() => 0)
      .finally(() => {
        if (radioRequest.current?.request === request)
          radioRequest.current = null;
      });
    radioRequest.current = { request, songs, generation };
    return request;
  }
  const similarRequestRef = useRef<Pending | null>(null);
  /** A queue that ended carries on with similar songs (#202); resolves to how
   * many arrived. */
  function continueWithSimilar(): Promise<number> {
    const joined = joinable(similarRequestRef.current);
    if (joined) return joined;
    const songs = queue.current;
    const generation = currentSession();
    const request: Promise<number> = api<{ tracks: Track[] }>(
      "/v1/radio/continuation",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(similarRequest(songs, cursor.current)),
      },
    )
      .then((batch) => {
        // A new queue, a stop or another account while waiting: these
        // songs belong to no one.
        if (queue.current !== songs || currentSession() !== generation)
          return 0;
        const added = freshSimilarSongs(
          songs,
          batch.tracks.map(fromSimilarTrack),
        );
        songs.push(...added);
        if (added.length) setQueueVersion((v) => v + 1);
        return added.length;
      })
      .catch(() => 0)
      .finally(() => {
        if (similarRequestRef.current?.request === request)
          similarRequestRef.current = null;
      });
    similarRequestRef.current = { request, songs, generation };
    return request;
  }
  /** Skipping a radio or similar song before it counts as a listen steers
   * what comes next. */
  function next() {
    if (skipSteersRadio(song, playEvent.current?.seconds, LISTEN_SECONDS))
      void api("/v1/radio/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_id: serverSongId(song), action: "skip" }),
      }).catch(() => undefined);
    step(1);
  }
  function play(songs: Song[], index = 0, more?: MoreSongs) {
    if (!songs[index]) return;
    resumeAt.current = 0;
    // The queue is its own copy: editing Up next never changes the page's
    // list it came from (Codex, #249).
    queue.current = [...songs];
    moreSource.current = more
      ? {
          songs: queue.current,
          generation: currentSession(),
          next: more,
          pending: null,
        }
      : null;
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
  function playNext(s: Song | Song[]) {
    const list = Array.isArray(s) ? s : [s];
    if (!list.length) return;
    if (!song) return play(list);
    for (const next of [...list].reverse())
      insertNext(queue.current, cursor.current, next);
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
  function step(delta: number, action?: StartAction) {
    // Past the loaded end of a queue with more to come, the next page is
    // read first; Repeat All wraps only once there is no more (Codex).
    if (
      delta > 0 &&
      cursor.current + delta >= queue.current.length &&
      moreFor(queue.current)
    ) {
      const songs = queue.current;
      const at = cursor.current;
      void readMore().then((added) => {
        if (queue.current !== songs || cursor.current !== at) return;
        // No more pages: the queue ends (or wraps) as any other does.
        if (added || !moreFor(songs)) step(delta, action);
      });
      return;
    }
    const index = stepIndex(
      queue.current.length,
      cursor.current,
      delta,
      repeat,
    );
    const next = queue.current[index];
    if (!next) {
      if (waitsForRadio(queue.current, cursor.current, delta)) {
        // The waiting Next belongs to this queue in this session too.
        const songs = queue.current;
        const at = cursor.current;
        const generation = currentSession();
        void refillRadio().then(
          (added) =>
            added &&
            queue.current === songs &&
            cursor.current === at &&
            currentSession() === generation &&
            step(delta, action),
        );
      } else if (
        continuesWithSimilar(
          queue.current,
          cursor.current,
          delta,
          repeat,
          keepPlaying(),
        )
      ) {
        const songs = queue.current;
        const at = cursor.current;
        void continueWithSimilar().then(
          (added) =>
            added &&
            queue.current === songs &&
            cursor.current === at &&
            step(delta, action),
        );
      }
      return;
    }
    resumeAt.current = 0;
    cursor.current = index;
    setQueueVersion((v) => v + 1);
    void load(next, action ?? (delta < 0 ? "previous" : "skip"));
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
  const hasNext =
    queueHasNext(queue.current, index, repeat) || !!moreFor(queue.current);
  useEffect(() => {
    if (!song?.url) return;
    const request = serial.current;
    void audio.current?.play().catch((e) => {
      const message = startFailure(e, request !== serial.current);
      // Waiting for a tap on Play is not the server's time to first sound.
      if (message) startClock.current.cancel(request);
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
            "Couldn't start the audio. Try another browser or add this song to your library.",
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
          onPlay={() => {
            startClock.current.asked(serial.current);
            setPlaying(true);
          }}
          onPlaying={() => {
            const heard = startClock.current.heard(
              serial.current,
              performance.now(),
            );
            if (heard)
              void apiFetch("/api/v1/performance/first-sound", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  ...heard,
                  source: startSource(song?.source),
                }),
              }).catch(() => undefined);
          }}
          onPause={() => {
            startClock.current.paused(serial.current);
            setPlaying(false);
          }}
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
              void load(queue.current[cursor.current], "next");
            else step(1, "next");
          }}
          onError={() => {
            if (song?.url)
              setError(
                "Couldn't play this audio stream. The provider or format may be unavailable.",
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
