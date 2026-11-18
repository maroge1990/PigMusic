import { useEffect, useState } from "react";
import { api, currentSession } from "./api";
import type { Song } from "./songs";
import type { Track } from "./types";

/**
 * Which songs are favourites, for the small star on every song row (D-118).
 * Read once from the Favourites playlist and again whenever the library
 * changes; every row shares the one answer.
 */
type Entry = Track & {
  saved_track?: Track | null;
  household_track?: Track | null;
};

let known: Set<string> | null = null;
let loading: Promise<void> | null = null;
let stale = false;
const listeners = new Set<() => void>();

const fold = (s = "") => s.toLocaleLowerCase().trim();
const nameKey = (title: string, artist: string) =>
  "n:" + fold(title) + "\u0000" + fold(artist);

function keysOf(entry: Entry): string[] {
  const keys = [nameKey(entry.title, entry.artist)];
  for (const t of [entry, entry.saved_track, entry.household_track]) {
    if (!t) continue;
    if (t.musicbrainz_recording_id)
      keys.push("r:" + t.musicbrainz_recording_id);
    if (t.id) keys.push("t:" + t.id);
  }
  return keys;
}

/** Whether `song` is one of `favourites` (the keys read from the list). */
export function isFavourite(song: Song, favourites: Set<string> | null) {
  if (!favourites) return false;
  const file = song.saved || song.server;
  if (file && favourites.has("t:" + file.id)) return true;
  if (song.recording_id) return favourites.has("r:" + song.recording_id);
  return favourites.has(nameKey(song.title, song.artist));
}

async function load() {
  // An answer for someone who has since signed out is never kept (Codex).
  const session = currentSession();
  const { playlists } = await api<{ playlists: { id: string }[] }>(
    "/v1/playlists",
  );
  const list = playlists.find((p) => p.id.startsWith("pig-favourites:"));
  const entries = list
    ? (await api<{ entries: Entry[] }>("/v1/playlists/" + list.id)).entries
    : [];
  if (session !== currentSession()) return;
  known = new Set(entries.flatMap(keysOf));
  publish();
}

function publish() {
  for (const listener of listeners) listener();
}

/** Reads the favourites again; resolves once the latest read is done. */
export function refreshFavourites(): Promise<void> {
  if (loading) {
    stale = true;
    return loading;
  }
  loading = load()
    .catch(() => {})
    .finally(() => {
      loading = null;
      if (stale) {
        stale = false;
        return refreshFavourites();
      }
    });
  return loading;
}

/** The favourites as last read, for tests and the rows. */
export const currentFavourites = () => known;

/** Another account signed in or out: forget the last one's stars at once,
 * then read the new account's while rows still show. */
function sessionCleared() {
  known = null;
  publish();
  if (listeners.size) void refreshFavourites();
}

let watching = false;
/** Starts following library and account changes (once). */
export function watchFavourites() {
  if (watching) return;
  watching = true;
  window.addEventListener("music-library-changed", () => {
    void refreshFavourites();
  });
  window.addEventListener("music-session-cleared", sessionCleared);
}

/** The favourites, shared by every row; null until first read. */
export function useFavourites() {
  const [value, setValue] = useState(known);
  useEffect(() => {
    const listener = () => setValue(known);
    listeners.add(listener);
    watchFavourites();
    if (!known && !loading) void refreshFavourites();
    else setValue(known);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return value;
}
