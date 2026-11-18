import { ApiError, api, isUnconfirmed } from "./api";
import { notifyError } from "./notices";
import { batches } from "./limits";
import { expectedSession, withSession } from "./sessionId";

export type Playlist = { id: string; name: string };

/** Your playlists, without Favourites (that one is the star). */
export async function loadPlaylists(): Promise<Playlist[]> {
  return (
    await api<{ playlists: Playlist[] }>("/v1/playlists")
  ).playlists.filter((p) => !p.id.startsWith("pig-favourites:"));
}

export function createPlaylist(name: string) {
  return api<Playlist>("/v1/playlists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

type Sent = { title?: string; artist?: string };
type Read = { revision: number; entries: Sent[] };

const fold = (s = "") => s.toLocaleLowerCase().trim();
const same = (a: Sent, b: Sent) =>
  fold(a.title) === fold(b.title) && fold(a.artist) === fold(b.artist);

/** A failure the notice area has already reported, with its Try again. */
export class ReportedFailure extends Error {}

/** No answer, or one that can't say whether the write was saved. */
const lost = (error: unknown) =>
  !(error instanceof ApiError) || isUnconfirmed(error);

/**
 * Adds songs to a playlist in order (Codex, #249). Each request appends a
 * batch all or none, and names the playlist revision it expects, so:
 * - Try again carries on from the first batch not confirmed, never from
 *   the start;
 * - after a lost answer the playlist is read: a revision one step on whose
 *   last songs are this batch means it landed; otherwise it is sent again.
 * Everything is sent under `session`.
 */
export async function addToPlaylist(
  id: string,
  bodies: Sent[],
  session = expectedSession(),
) {
  const parts = batches(bodies);
  const bound = <T>(start: () => Promise<T>) => withSession(session, start);
  const read = () => bound(() => api<Read>(`/v1/playlists/${id}`));
  const send = (tracks: Sent[], revision: number) =>
    bound(() =>
      api<Read>(
        `/v1/playlists/${id}/songs?allow_repeat=true&revision=${revision}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tracks }),
        },
      ),
    );
  async function sendFrom(start: number): Promise<void> {
    let revision = (await read()).revision;
    for (let i = start; i < parts.length; i++) {
      let answered: Read | null = null;
      let failure: unknown = null;
      // One more go after a lost answer or a moved revision, never more.
      for (let attempt = 0; attempt < 2 && !answered; attempt++) {
        try {
          answered = await send(parts[i], revision);
        } catch (e) {
          failure = e;
          // A 409: the playlist moved (or is full); it was not changed.
          const moved = e instanceof ApiError && e.code === "conflict";
          if (!lost(e) && !moved) break;
          const now = await read().catch(() => null);
          if (!now) break;
          const tail = now.entries.slice(-parts[i].length);
          if (
            lost(e) &&
            now.revision === revision + 1 &&
            tail.length === parts[i].length &&
            tail.every((entry, k) => same(entry, parts[i][k]))
          )
            answered = now;
          else revision = now.revision;
        }
      }
      if (!answered) {
        window.dispatchEvent(new Event("music-library-changed"));
        const added = parts.slice(0, i).flat().length;
        notifyError(
          added
            ? `Added ${added} of ${bodies.length} songs to the playlist. Couldn't add the rest. Try again.`
            : (failure as Error).message,
          () => sendFrom(i),
        );
        throw new ReportedFailure((failure as Error).message);
      }
      revision = answered.revision;
    }
    window.dispatchEvent(new Event("music-library-changed"));
  }
  await sendFrom(0);
}
