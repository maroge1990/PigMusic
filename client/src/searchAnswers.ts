/** Search answers by query, reused while fresh so backspacing or retyping
 * sends no request. They belong to the signed-in person: every account change
 * empties them, and an answer that arrives after one is dropped. Library
 * answers are also dropped when the library changes. */
import { currentSession, sessionMap } from "./api";
import type { Album, Artist, Track } from "./types";

export type CatalogueArtist = {
  id: string;
  name: string;
  description?: string;
};
export type FoundAlbum = Album & {
  in_library: boolean;
  server_state?: Track["server_state"];
};
export type FoundArtist = Artist & { in_library: boolean };
export type FoundPlaylist = { id: string; name: string; track_count: number };
/** One ranked search over your library and the server (D-61): each song,
 * album and artist once, marked in your library or not. */
export type Local = {
  top: { kind: "song" | "album" | "artist" | "playlist"; id: string }[];
  songs: Track[];
  albums: FoundAlbum[];
  artists: FoundArtist[];
  playlists: FoundPlaylist[];
};
type Answer = { time: number; local?: Local; catalogue?: CatalogueArtist[] };

const ANSWER_LIFETIME_MS = 5 * 60_000;
const answers = sessionMap<string, Answer>();

window.addEventListener("music-library-changed", () => {
  for (const answer of answers.values()) delete answer.local;
});

export function answer(key: string, now = Date.now()) {
  const found = answers.get(key);
  if (found && now - found.time > ANSWER_LIFETIME_MS) {
    answers.delete(key);
    return undefined;
  }
  return found;
}

/** Keep part of an answer, unless the session that asked has ended. */
export function remember(
  key: string,
  session: number,
  update: (entry: Answer) => void,
  now = Date.now(),
) {
  if (session !== currentSession()) return false;
  if (!answers.has(key) && answers.size >= 60)
    answers.delete(answers.keys().next().value!);
  const entry = answers.get(key) || { time: now };
  update(entry);
  answers.set(key, entry);
  return true;
}

const RECENT_LIMIT = 8;
const recentKey = (person: string) => "pig-music-recent-searches:" + person;

/** This person's recent searches on this browser, newest first. */
export function recentSearches(
  person: string,
  storage: Pick<Storage, "getItem"> = localStorage,
): string[] {
  try {
    const value = JSON.parse(storage.getItem(recentKey(person)) || "[]");
    return Array.isArray(value)
      ? value.filter((v) => typeof v === "string").slice(0, RECENT_LIMIT)
      : [];
  } catch {
    return [];
  }
}

/** Save the recent searches (an empty list forgets them). */
export function saveRecentSearches(
  person: string,
  list: string[],
  storage: Pick<Storage, "setItem" | "removeItem"> = localStorage,
) {
  try {
    if (list.length)
      storage.setItem(
        recentKey(person),
        JSON.stringify(list.slice(0, RECENT_LIMIT)),
      );
    else storage.removeItem(recentKey(person));
  } catch {
    // Private browsing: nothing is remembered.
  }
}

/** The list with this query first, once, ignoring case. */
export function withRecent(list: string[], query: string) {
  const q = query.trim();
  if (!q) return list;
  const fold = q.toLocaleLowerCase();
  return [q, ...list.filter((v) => v.toLocaleLowerCase() !== fold)].slice(
    0,
    RECENT_LIMIT,
  );
}
