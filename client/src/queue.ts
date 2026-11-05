import type { Song } from "./songs";

/** The rules of the play queue, kept apart from the audio element and React
 * so they can be tested on their own (#97). A queue is a list of songs and a
 * cursor at the one playing; the editing helpers change the list in place,
 * as the player keeps one list for the whole visit. */

/** Like iPhone: off, the whole queue again, or the current song again. */
export type RepeatMode = "off" | "all" | "one";

export const QUEUE_KEY = "pig-music-queue";
export const REPEAT_KEY = "pig-music-repeat";
/** At most this many songs are saved for the next visit. */
const SAVED_SONGS = 500;
/** Radio asks for more once this few songs are left after the current one. */
const RADIO_LOW_WATER = 5;
/** How many upcoming streamed songs the server prepares ahead. */
const PREPARE_AHEAD = 2;
/** Previous restarts the song once more than this many seconds were heard. */
const RESTART_AFTER = 3;

export type SavedQueue = { songs: Song[]; cursor: number; position: number };

/** The queue saved by an earlier visit, or null when there is none to resume. */
export function parseSavedQueue(text: string | null): SavedQueue | null {
  try {
    const saved = JSON.parse(text || "null");
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

/** What to save for the next visit, or null when the saved queue goes. */
export function serializeQueue(
  songs: Song[],
  cursor: number,
  position: number,
): string | null {
  if (!songs.length || cursor < 0) return null;
  return JSON.stringify({
    songs: songs.slice(0, SAVED_SONGS),
    cursor: Math.min(cursor, SAVED_SONGS - 1),
    position,
  });
}

export function parseRepeat(saved: string | null): RepeatMode {
  return saved === "all" || saved === "one" ? saved : "off";
}

export function nextRepeat(repeat: RepeatMode): RepeatMode {
  return repeat === "off" ? "all" : repeat === "all" ? "one" : "off";
}

/** The index a skip of `delta` lands on. Repeat all wraps around the queue in
 * both directions; otherwise it may fall outside the queue (nothing there). */
export function stepIndex(
  length: number,
  cursor: number,
  delta: number,
  repeat: RepeatMode,
): number {
  const index = cursor + delta;
  return repeat === "all" && length ? (index + length) % length : index;
}

/** Radio has no end: past its last song it waits for more instead of stopping. */
export function waitsForRadio(
  songs: Song[],
  cursor: number,
  delta: number,
): boolean {
  return delta > 0 && !!songs[cursor]?.radio;
}

/** What happens when a song plays to its end. */
export function afterEnd(repeat: RepeatMode): "again" | "next" {
  // Repeat one plays the song again as a new play.
  return repeat === "one" ? "again" : "next";
}

export function hasNext(
  songs: Song[],
  cursor: number,
  repeat: RepeatMode,
): boolean {
  return (
    cursor >= 0 &&
    (cursor < songs.length - 1 ||
      !!songs[cursor]?.radio ||
      (repeat === "all" && songs.length > 0))
  );
}

/** Previous is offered while a song is loaded (it restarts) or one comes before. */
export function canGoPrevious(
  busy: boolean,
  loaded: boolean,
  cursor: number,
): boolean {
  return !busy && (loaded || cursor > 0);
}

/** Previous restarts the song once a few seconds were heard, or at the start
 * of the queue; otherwise it goes back a song. */
export function previousRestarts(currentTime: number, cursor: number): boolean {
  return currentTime > RESTART_AFTER || cursor <= 0;
}

/** A radio song skipped before it counts as a listen steers the station. */
export function skipSteersRadio(
  song: Song | null | undefined,
  heardSeconds: number | undefined,
  listenSeconds: number,
): boolean {
  return (
    !!song?.radio &&
    !!song.cache_id &&
    !(heardSeconds !== undefined && heardSeconds >= listenSeconds)
  );
}

/** Radio asks for its next songs once few are left after the current one. */
export function radioRunningLow(
  song: Song,
  songs: Song[],
  cursor: number,
): boolean {
  return !!song.radio && songs.length - 1 - cursor <= RADIO_LOW_WATER;
}

/** The radio song that the next batch continues from. */
export function lastRadioSong(songs: Song[]): Song | undefined {
  return [...songs].reverse().find((s) => s.radio);
}

/** A radio batch without the songs the queue already holds. */
export function freshRadioSongs(songs: Song[], batch: Song[]): Song[] {
  const known = new Set(songs.map((s) => s.cache_id));
  return batch.filter((s) => !known.has(s.cache_id));
}

/** The next streamed songs, which the server can fetch before they are reached. */
export function upcomingToPrepare(songs: Song[], cursor: number): Song[] {
  return songs
    .slice(cursor + 1, cursor + 1 + PREPARE_AHEAD)
    .filter((s) => !s.cache_id && !s.saved && s.state !== "server");
}

/** Play next: straight after the current song. */
export function insertNext(songs: Song[], cursor: number, song: Song): void {
  songs.splice(cursor + 1, 0, song);
}

/** Moves an upcoming song by `delta`; the current and earlier songs stay put.
 * Returns whether anything moved. */
export function moveUpcoming(
  songs: Song[],
  cursor: number,
  at: number,
  delta: number,
): boolean {
  const target = at + delta;
  if (target <= cursor || target >= songs.length) return false;
  const [item] = songs.splice(at, 1);
  songs.splice(target, 0, item);
  return true;
}

/** Removes an upcoming song; the current and earlier songs stay. */
export function removeUpcoming(
  songs: Song[],
  cursor: number,
  at: number,
): boolean {
  if (at <= cursor) return false;
  songs.splice(at, 1);
  return true;
}
