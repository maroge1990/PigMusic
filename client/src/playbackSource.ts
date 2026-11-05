import { apiFetch } from "./api";
import { songBody, type Song } from "./songs";

/** Where a song's audio comes from, as the player plays it. */
export type Resolved = {
  url: string;
  source: string;
  note?: string;
  /** Library track behind the audio, for play history. */
  saved_id?: string;
};

/** The song in the player, with its audio once resolved. */
export type Playing = Song & Partial<Resolved>;

/** Resolve the audio for one song: your file, or a stream when unsaved. */
export async function resolve(s: Song): Promise<Resolved> {
  if (s.cache_id) {
    const found = await resolveSaved(s.cache_id);
    return { ...found, source: "Your mix" };
  }
  // Household files play straight from the server without being added.
  if (s.state === "server" && s.server) return resolveSaved(s.server.id);
  if (s.saved) return resolveSaved(s.saved.id);
  if (s.release_id && (s.track_id || s.recording_id)) {
    const response = await apiFetch("/api/v1/remote-playback/catalogue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        release_id: s.release_id,
        track_id: s.track_id,
        recording_id: s.track_id ? undefined : s.recording_id,
      }),
    });
    const body = await response.json();
    if (!response.ok)
      throw new Error(
        typeof body.detail === "string"
          ? body.detail
          : "This song can't be streamed right now.",
      );
    return {
      url: body.url as string,
      source: body.source as string,
      note: body.note as string | undefined,
      saved_id:
        body.history_track_id ||
        (body.source === "Saved library" ? (body.id as string) : undefined),
    };
  }
  const response = await apiFetch("/api/v1/remote-playback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(songBody(s)),
  });
  const body = await response.json();
  if (!response.ok)
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "This song can't be streamed right now.",
    );
  return {
    url: body.url as string,
    source: body.source as string,
    note: body.note as string | undefined,
    saved_id: body.history_track_id as string | undefined,
  };
}

async function resolveSaved(id: string): Promise<Resolved> {
  const response = await apiFetch(
    "/api/v1/playback/" + encodeURIComponent(id) + "/url",
    { method: "POST" },
  );
  const body = await response.json();
  if (!response.ok)
    throw new Error(
      typeof body.detail === "string" ? body.detail : "Playback unavailable.",
    );
  return { url: body.url as string, source: "Saved library", saved_id: id };
}
