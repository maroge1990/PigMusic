import { api, currentSession } from "./api";
import type { MoreSongs } from "./Player";
import { fromTrack, type Song } from "./songs";
import type { Track } from "./types";

export type LibraryPage = {
  items: Track[];
  total: number;
  next_offset: number | null;
  revision: string;
};
export const LIBRARY_PAGE_SIZE = 200;

export const readLibraryPage = (offset: number) =>
  api<LibraryPage>(
    `/v1/library-pages/tracks?offset=${offset}&limit=${LIBRARY_PAGE_SIZE}`,
  );

/**
 * Your library in the server's order as one source of songs for the player
 * (#251, Codex): each call answers the next playable songs, or none at the
 * end. It reads on past pages with nothing playable. A page from a later
 * library state than the last starts again from the top, and only songs this
 * source has not given yet are answered, so nothing repeats. An answer that
 * arrives after the session ended ends the source.
 */
export function librarySource(
  from: {
    /** Where the songs already given end. */
    next?: number | null;
    /** The library state those songs came from. */
    revision?: string;
    /** Songs given already (by their keys). */
    given?: Iterable<string>;
  } = {},
): MoreSongs {
  let next = from.next === undefined ? 0 : from.next;
  let revision = from.revision || "";
  const given = new Set(from.given || []);
  const generation = currentSession();
  return async () => {
    while (next !== null) {
      const offset: number = next;
      const page = await readLibraryPage(offset);
      if (currentSession() !== generation) return [];
      if (revision && page.revision !== revision && offset > 0) {
        // The library changed: read again from the top, skipping songs
        // already given.
        revision = page.revision;
        next = 0;
        continue;
      }
      revision = page.revision;
      next = page.next_offset;
      const songs: Song[] = page.items
        .filter((t) => t.available !== false && !given.has(t.id))
        .map((t) => fromTrack(t));
      songs.forEach((s) => given.add(s.key));
      if (songs.length) return songs;
    }
    return [];
  };
}
