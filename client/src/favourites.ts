import { useEffect, useState } from "react";
import { api } from "./api";
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
  const { playlists } = await api<{ playlists: { id: string }[] }>(
    "/v1/playlists",
  );
  const list = playlists.find((p) => p.id.startsWith("pig-favourites:"));
  const entries = list
    ? (await api<{ entries: Entry[] }>("/v1/playlists/" + list.id)).entries
    : [];
  known = new Set(entries.flatMap(keysOf));
  for (const listener of listeners) listener();
}

function refresh() {
  if (loading) {
    stale = true;
    return;
  }
  loading = load()
    .catch(() => {})
    .finally(() => {
      loading = null;
      if (stale) {
        stale = false;
        refresh();
      }
    });
}

let watching = false;
function watch() {
  if (watching) return;
  watching = true;
  window.addEventListener("music-library-changed", refresh);
}

/** The favourites, shared by every row; null until first read. */
export function useFavourites() {
  const [value, setValue] = useState(known);
  useEffect(() => {
    const listener = () => setValue(known);
    listeners.add(listener);
    watch();
    if (!known && !loading) refresh();
    else setValue(known);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return value;
}
