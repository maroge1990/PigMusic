/**
 * What a catalogue album's page and its card's menu ask the server (#267,
 * D-129): the edition a release group opens on with each song's state, and
 * Add to library for the songs not yet yours.
 */
import { apiFetch, currentSession, detailOr, writeFailure } from "./api";
import {
  addable,
  addedMessage,
  checking,
  lookedUp,
  markYours,
  nothingToAdd,
  openingEdition,
  popularRows,
  type PopularSong,
  releaseArtwork,
  releaseSongs,
  type CatalogueRelease,
  type Editions,
} from "./catalogueAlbum";
import { notify, notifyError } from "./notices";
import { withSession } from "./sessionId";
import { songBody, withLibrary, type AddResult, type Song } from "./songs";

export const NO_EDITIONS = "No editions of this album are available.";
export const LOOKUP_FAILED = "Couldn't check your library. Try again.";
/** A card whose songs couldn't be read, with no sentence of the server's to
 * say why (the iPhone's words, D-117). */
export const couldntLoad = (name: string) =>
  `Couldn't load ${name}. Try again.`;

/** The server's own sentence for a catalogue read it refused. */
export class CatalogueRefusal extends Error {}

/** A catalogue read; a refusal carries the server's words. */
export async function catalogueRequest<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await apiFetch("/api/v1/catalogue/" + path, options);
  const data = await response.json();
  if (!response.ok)
    throw typeof data.detail === "string"
      ? new CatalogueRefusal(data.detail)
      : new Error("Catalogue unavailable");
  return data;
}

/** A card's lookup failure as the card says it: the server's own sentence
 * when it gave one, else "Couldn't load <name>. Try again." */
export function cardFailure(error: unknown, name: string): Error {
  return error instanceof CatalogueRefusal ||
    (error as Error)?.message === NO_EDITIONS
    ? (error as Error)
    : new Error(couldntLoad(name));
}

/**
 * A catalogue artist's popular songs, as the artist's page lists them
 * (ArtistPopular): the songs yours play from your library, the rest as the
 * page's rows do. No popular songs is a failure: there is nothing to play.
 */
export async function catalogueArtistSongs(artistId: string): Promise<Song[]> {
  const body = await catalogueRequest<{ songs?: PopularSong[] }>(
    `artists/${encodeURIComponent(artistId)}/popularity`,
  );
  const plain = popularRows(artistId, body.songs || []);
  if (!plain.length) throw new Error("No popular songs");
  // As on the page: a failed lookup leaves the songs as the chart has them.
  return withLibrary(plain).catch(() => plain);
}

/**
 * A release group's songs as its page would show them: the edition it opens
 * on (never another one: live, remix and demo editions stay distinct), each
 * song's state from the release answer and then the library lookup. If the
 * lookup fails the songs that could be added stay `checking`, as on the page:
 * they play, but Add won't send them (#263).
 */
export async function catalogueAlbumSongs(
  groupId: string,
  artistId: string,
): Promise<Song[]> {
  const editions = await catalogueRequest<Editions>(
    `groups/${encodeURIComponent(groupId)}/editions`,
  );
  const opening = openingEdition(editions);
  if (!opening) throw new Error(NO_EDITIONS);
  const release = await catalogueRequest<CatalogueRelease>(
    `releases/${encodeURIComponent(opening)}`,
  );
  const plain = releaseSongs(release, artistId, releaseArtwork(release.id));
  try {
    return markYours(plain, await withLibrary(lookedUp(plain)));
  } catch {
    return checking(plain);
  }
}

/** The album page's Add to library request: only the songs it can add. */
export async function requestAlbum(rows: Song[]): Promise<AddResult> {
  const response = await apiFetch("/api/v1/requests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // Only songs you can add: one already yours would come back as
    // skipped, and resending one on its way would resume it if paused.
    body: JSON.stringify({ tracks: rows.filter(addable).map(songBody) }),
  });
  if (!response.ok)
    throw await writeFailure(
      response,
      detailOr("Couldn't add this album. Try again."),
    );
  return (await response.json()) as AddResult;
}

/**
 * Add to library from a catalogue album's card, as its page does it: the
 * same request and the same words, sent under the session that chose it.
 * Answers the rows as they now stand (the songs sent are being added), or
 * null when nothing was sent or the answer came to another session.
 */
export async function addCatalogueAlbum(
  rows: Song[],
  session: string | null,
  tryAgain?: () => unknown,
): Promise<Song[] | null> {
  if (rows.some((r) => r.checking)) {
    notifyError(LOOKUP_FAILED, tryAgain, "library-lookup");
    return null;
  }
  if (!rows.some(addable)) {
    notify(nothingToAdd(rows));
    return rows;
  }
  const generation = currentSession();
  try {
    const result = await withSession(session, () => requestAlbum(rows));
    if (currentSession() !== generation) return null;
    notify(addedMessage(result));
    window.dispatchEvent(new Event("music-library-changed"));
    window.dispatchEvent(new Event("music-requests-changed"));
    return rows.map((r) =>
      addable(r) ? { ...r, state: "pending" as const } : r,
    );
  } catch (e) {
    if (currentSession() === generation) notifyError((e as Error).message);
    return null;
  }
}
