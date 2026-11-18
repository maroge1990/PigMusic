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
type Read = { revision: number; name?: string };

/** A failure the notice area has already reported. */
export class ReportedFailure extends Error {}

/** No answer, or one that can't say whether the write was saved. */
const lost = (error: unknown) =>
  !(error instanceof ApiError) || isUnconfirmed(error);

/**
 * Adds songs to a playlist in order (Codex, #249). Each request appends a
 * batch all or none at the playlist revision it expects.
 * - A refusal (the server answered: an error, or 409 because the playlist
 *   moved) offers Try again from that batch, never from the start, and
 *   keeps offering it until the copy is done or the notice is closed.
 * - A lost answer can't be told apart from a saved batch, so the copy
 *   stops and says so, with no resend.
 * Everything is sent under `session`.
 */
export async function addToPlaylist(
  id: string,
  bodies: Sent[],
  session = expectedSession(),
) {
  const parts = batches(bodies);
  const bound = <T>(start: () => Promise<T>) => withSession(session, start);
  let name = "the playlist";
  const changed = () =>
    window.dispatchEvent(new Event("music-library-changed"));
  const songs = (n: number) => `${n} ${n === 1 ? "song" : "songs"}`;
  /** Says what failed and offers Try again from batch `at`. */
  function refused(at: number, error: unknown): never {
    const added = parts.slice(0, at).flat().length;
    notifyError(
      added
        ? `Added ${added} of ${bodies.length} songs to ${name}. Couldn't add the rest. Try again.`
        : (error as Error).message,
      () =>
        sendFrom(at).catch((e) => {
          if (!(e instanceof ReportedFailure)) throw e;
        }),
    );
    throw new ReportedFailure((error as Error).message);
  }
  async function sendFrom(start: number): Promise<void> {
    let revision: number;
    try {
      const playlist = await bound(() => api<Read>(`/v1/playlists/${id}`));
      revision = playlist.revision;
      if (playlist.name) name = playlist.name;
    } catch (e) {
      // Not even read: nothing was sent, so the same Try again stays.
      refused(start, e);
    }
    for (let i = start; i < parts.length; i++) {
      try {
        revision = (
          await bound(() =>
            api<Read>(
              `/v1/playlists/${id}/songs?allow_repeat=true&revision=${revision}`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tracks: parts[i] }),
              },
            ),
          )
        ).revision;
      } catch (e) {
        changed();
        if (lost(e)) {
          notifyError(
            `Couldn't tell whether the last ${songs(parts[i].length)} were added to ${name}. Check the playlist.`,
          );
          throw new ReportedFailure((e as Error).message);
        }
        refused(i, e);
      }
    }
    changed();
  }
  await sendFrom(0);
}
