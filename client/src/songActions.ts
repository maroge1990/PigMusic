import { ApiError, api, currentSession } from "./api";
import { leftOutMessage, type LeftOut } from "./queueLimits";
import { batches } from "./limits";
import { expectedSession, withSession } from "./sessionId";
import { notify, notifyError, offerUndo } from "./notices";
import type { RemovalPreview } from "./removal";
import { PAUSED_NOTICE, songBody, type AddResult, type Song } from "./songs";

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
/**
 * Runs a collection action on its songs under the session that chose it
 * (Codex, #249): the session is taken before the songs are read, and the
 * action is dropped if another account signed in meanwhile.
 */
export async function withCollection(
  songs: () => Promise<Song[]>,
  act: (list: Song[], session: string | null) => unknown,
) {
  const session = expectedSession();
  const list = await songs();
  if (expectedSession() !== session) return;
  return act(list, session);
}

export async function addSongsToLibrary(
  songs: Song[],
  /** The session that asked; every batch and Try again is sent under it. */
  session = expectedSession(),
) {
  const wanted = songs.filter(
    (s) => s.state === "missing" || s.state === "server",
  );
  if (!wanted.length) return notify("Already in your library");
  // A large collection goes in batches within the server's limit (Codex),
  // every one under the session that asked.
  const count = (n: number) => `${n} ${n === 1 ? "song" : "songs"}`;
  let paused = 0;
  async function send(rest: Song[], addedBefore: number): Promise<void> {
    let adding = addedBefore;
    // Songs a queue limit left out (D-128), and the answer that named it.
    let leftOut: Song[] = [];
    let limit: LeftOut = {};
    const parts = batches(rest);
    for (let i = 0; i < parts.length; i++) {
      try {
        const result = await withSession(session, () =>
          api<AddResult>("/v1/requests", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tracks: parts[i].map(songBody) }),
          }),
        );
        adding += result.created + result.existing;
        paused += result.paused || 0;
        if (result.left_out) {
          leftOut = leftOut.concat(
            parts[i].filter((_, j) => result.outcomes?.[j] === "over_limit"),
          );
          limit = result;
        }
      } catch (e) {
        const left = parts.slice(i).flat();
        if (adding) changed();
        if (e instanceof ApiError && e.code === "queue_limit") {
          // Every song of this batch was past a limit: the later ones
          // would be too. Try again sends all those left out.
          return limited(adding, leftOut.concat(left), e.body as LeftOut);
        }
        notifyError(
          adding
            ? `Adding ${count(adding)} to your library. Couldn't add the other ${count(left.length)}. Try again.`
            : (e as Error).message,
          () => send(left, adding),
        );
        return;
      }
    }
    if (leftOut.length) return limited(adding, leftOut, limit);
    notify(
      [
        adding && `Adding ${count(adding)} to your library.`,
        paused && PAUSED_NOTICE,
      ]
        .filter(Boolean)
        .join(" ") || "Nothing to add",
    );
    changed();
  }
  /** Some songs past a queue limit: what was added, and how many weren't,
   * with Try again for those. */
  function limited(adding: number, left: Song[], limit: LeftOut) {
    if (adding) changed();
    notifyError(
      [
        adding && `Adding ${count(adding)} to your library.`,
        paused && PAUSED_NOTICE,
        leftOutMessage(limit, left.length),
      ]
        .filter(Boolean)
        .join(" "),
      () => send(left, adding),
    );
  }
  function changed() {
    window.dispatchEvent(new Event("music-library-changed"));
    window.dispatchEvent(new Event("music-requests-changed"));
  }
  await send(wanted, 0);
}

/**
 * Removes songs from your library at once and offers Undo (D-79, D-118);
 * the files stay on the server for anyone else who has them.
 */
export async function removeFromLibrary(ids: string[], after?: () => void) {
  // The preview, the removal and its Undo all belong to the session that
  // asked (#250, Codex): a sign-in in between can't receive any of them.
  const session = expectedSession();
  const bound = <T>(start: () => Promise<T>) => withSession(session, start);
  // An answer that arrives after a sign-out or account change ends here
  // without a word: no Undo, error or refresh reaches the next session.
  const generation = currentSession();
  const ended = () => currentSession() !== generation;
  try {
    const preview = await bound(() =>
      api<RemovalPreview>("/v1/library/removal-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_ids: ids }),
      }),
    );
    if (ended()) return;
    const removed = await bound(() =>
      api<{ id: string; undo_until: number }>("/v1/library/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          track_ids: ids,
          confirmation: preview.confirmation,
        }),
      }),
    );
    if (ended()) return;
    offerUndo(
      "Removed from your library.",
      async () => {
        await bound(() =>
          api(`/v1/library/removals/${removed.id}/undo`, { method: "POST" }),
        );
        window.dispatchEvent(new Event("music-library-changed"));
      },
      "Restored",
      undefined,
      removed.undo_until * 1000 - Date.now(),
    );
    after?.();
    window.dispatchEvent(new Event("music-library-changed"));
  } catch (e) {
    if (!ended()) notifyError((e as Error).message);
  }
}
