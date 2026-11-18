import { api } from "./api";

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

/** Adds songs to a playlist in order, then says the library changed. */
export async function addToPlaylist(id: string, bodies: object[]) {
  for (const body of bodies)
    await api(`/v1/playlists/${id}/songs?allow_repeat=true`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  window.dispatchEvent(new Event("music-library-changed"));
}
