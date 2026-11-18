import { api } from "./api";
import { notify, notifyError, offerUndo } from "./notices";
import type { RemovalPreview } from "./removal";
import { songBody, type AddResult, type Song } from "./songs";

/** The file the server holds for a song: yours, the household's or a mix's. */
export function songFile(song: Song): string | null {
  return (song.saved || song.server)?.id || song.cache_id || null;
}

/** Saves songs' files to this computer, one browser download each. */
export function downloadSongs(songs: Song[]) {
  const files = songs.filter((s) => songFile(s));
  if (!files.length)
    return notifyError("Couldn't download: nothing here is on the server yet.");
  files.forEach((song, i) =>
    setTimeout(() => {
      const link = document.createElement("a");
      link.href = `/api/v1/tracks/${encodeURIComponent(songFile(song)!)}/file`;
      link.download = `${song.artist} - ${song.title}`;
      document.body.append(link);
      link.click();
      link.remove();
    }, i * 300),
  );
  notify(
    files.length === 1
      ? `Downloading ${files[0].title}`
      : `Downloading ${files.length} songs`,
  );
}

/** Favourites or unfavourites a song, and says so. */
export async function setFavourite(song: Song, favourite: boolean) {
  try {
    await api<{ favourited: boolean }>("/v1/favourites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track: songBody(song), favourite }),
    });
    notify(
      favourite ? "Added to favourites" : "Removed from favourites",
      "favourite",
    );
    window.dispatchEvent(new Event("music-library-changed"));
  } catch (e) {
    notifyError((e as Error).message);
  }
}

/** Asks the server for the songs not yet yours ("Add to library", D-115). */
export async function addSongsToLibrary(songs: Song[]) {
  const wanted = songs.filter(
    (s) => s.state === "missing" || s.state === "server",
  );
  if (!wanted.length) return notify("Already in your library");
  try {
    const result = await api<AddResult>("/v1/requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tracks: wanted.map(songBody) }),
    });
    const adding = result.created + result.existing;
    notify(
      adding
        ? `Adding ${adding} ${adding === 1 ? "song" : "songs"} to your library`
        : "Nothing to add",
    );
    window.dispatchEvent(new Event("music-library-changed"));
    window.dispatchEvent(new Event("music-requests-changed"));
  } catch (e) {
    notifyError((e as Error).message);
  }
}

/**
 * Removes songs from your library at once and offers Undo (D-79, D-118);
 * the files stay on the server for anyone else who has them.
 */
export async function removeFromLibrary(ids: string[], after?: () => void) {
  try {
    const preview = await api<RemovalPreview>("/v1/library/removal-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_ids: ids }),
    });
    const removed = await api<{ id: string; undo_until: number }>(
      "/v1/library/remove",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          track_ids: ids,
          confirmation: preview.confirmation,
        }),
      },
    );
    offerUndo(
      "Removed from your library.",
      async () => {
        await api(`/v1/library/removals/${removed.id}/undo`, {
          method: "POST",
        });
        window.dispatchEvent(new Event("music-library-changed"));
      },
      "Restored",
      undefined,
      removed.undo_until * 1000 - Date.now(),
    );
    after?.();
    window.dispatchEvent(new Event("music-library-changed"));
  } catch (e) {
    notifyError((e as Error).message);
  }
}
