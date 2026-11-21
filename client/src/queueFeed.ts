/**
 * The person's requests as the server's change feed last sent them (the
 * `queue` event of /api/v1/events, relayed by the shell as `music-queue`).
 * Catalogue cards compare it with what they last read, so a card reads its
 * album again only when one of its own songs' requests changed state, never
 * for download progress or anyone else's song (Codex round 2 on #267).
 */
import { currentSession } from "./api";

/** One request as the feed lists it (the fields used here). */
export type QueueItem = {
  id?: string;
  status?: string;
  title?: string;
  musicbrainz_recording_id?: string | null;
  musicbrainz_release_id?: string | null;
};

let latest: { at: number; items: QueueItem[] } | null = null;

/** The feed's latest list, for the session signed in now; null until the
 * feed has sent one. */
export function currentQueue(): QueueItem[] | null {
  return latest && latest.at === currentSession() ? latest.items : null;
}

/** Keeps a list the feed sent. The shell calls it through `music-queue`. */
export function rememberQueue(items: QueueItem[]) {
  latest = { at: currentSession(), items };
}

if (typeof window !== "undefined")
  // Registered when this module loads, before any card listens, so a card
  // always compares against the list that has just arrived.
  window.addEventListener("music-queue", (event) => {
    const items = (event as CustomEvent<{ items?: QueueItem[] }>).detail?.items;
    if (Array.isArray(items)) rememberQueue(items);
  });

/** Songs as a card knows them. */
type Known = {
  title: string;
  release_id?: string;
  recording_id?: string;
};

/**
 * Where these songs' requests stand in a list: each request naming one of
 * them, by id and state only, so download progress changes nothing. The
 * same songs and an unchanged answer give the same text.
 */
export function queueSignature(
  songs: Known[],
  items: QueueItem[] | null,
): string {
  if (!items) return "";
  const recordings = new Set(songs.map((s) => s.recording_id).filter(Boolean));
  const names = new Set(
    songs.map((s) => `${s.release_id}|${s.title.toLocaleLowerCase()}`),
  );
  return items
    .filter(
      (item) =>
        (item.musicbrainz_recording_id &&
          recordings.has(item.musicbrainz_recording_id)) ||
        names.has(
          `${item.musicbrainz_release_id}|${(item.title || "").toLocaleLowerCase()}`,
        ),
    )
    .map((item) => `${item.id}:${item.status}`)
    .sort()
    .join(",");
}
