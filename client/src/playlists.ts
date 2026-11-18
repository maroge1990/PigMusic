import { api } from "./api";
import { batches } from "./limits";

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

/** Adds songs to a playlist in order, all in one request (Codex, #249): the
 * server appends them together or not at all, so trying again after a
 * failure never doubles the songs already in. Only a collection larger
 * than one request's limit goes in several. */
export async function addToPlaylist(id: string, bodies: object[]) {
  for (const tracks of batches(bodies))
    await api(`/v1/playlists/${id}/songs?allow_repeat=true`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tracks }),
    });
  window.dispatchEvent(new Event("music-library-changed"));
}
