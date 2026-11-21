/**
 * A catalogue album, as its page and its card both see it (#263, #267,
 * D-129): which edition a release group opens on, each song's state, what
 * Add to library would add, and the words for what it did.
 */
import type { BadgeState } from "./StateBadge";
import {
  PAUSED_NOTICE,
  type AddOutcome,
  type AddResult,
  type Song as Row,
} from "./songs";
import type { Track } from "./types";

export type CatalogueSong = {
  id: string;
  title: string;
  artist: string;
  duration: number;
  year?: string | number;
  disc_number: number;
  track_number: number;
  status: string;
  presence_note: string;
  server_state?:
    | "not_in_library"
    | "household_available"
    | "requested"
    | "in_library"
    | "failed";
  saved_track?: Track | null;
  household_track?: Track | null;
  received_bytes?: number;
  total_bytes?: number;
  musicbrainz_recording_id?: string;
  /** On an edition other than the recommended one: requests keep it. */
  explicit_edition?: boolean;
  /** A Live, Remix or Demo release's songs are those recordings. */
  recording_kind?: string[];
};

export type CatalogueRelease = {
  id: string;
  title: string;
  artist: string;
  date: string;
  description: string;
  tracks: CatalogueSong[];
};

export type Editions<E extends { id: string } = { id: string }> = {
  editions: E[];
  total: number;
  recommended_id?: string;
};

/** The edition a release group opens on: the server's recommended one, else
 * the first it lists; none when it lists none. */
export function openingEdition(answer: Editions): string | undefined {
  return answer.editions.length
    ? answer.recommended_id || answer.editions[0].id
    : undefined;
}

// A song the server is still adding for you.
export const pending = (song: { server_state?: string }) =>
  song.server_state === "requested";

/** A release's songs as rows; `cover` is the release's artwork. */
export function releaseSongs(
  release: CatalogueRelease,
  artistId: string,
  cover: string,
): Row[] {
  return release.tracks.map((t) => ({
    key: release.id + ":" + t.id,
    title: t.title,
    artist: t.artist,
    album: release.title,
    album_artist: release.artist,
    duration: t.duration,
    year: t.year,
    track_number: t.track_number,
    disc_number: t.disc_number,
    cover: t.saved_track?.cover_url || cover,
    // The server says where each song stands (CatalogueTrackDTO).
    state:
      t.server_state === "in_library" && t.saved_track
        ? "saved"
        : t.server_state === "household_available" && t.household_track
          ? "server"
          : pending(t)
            ? "pending"
            : "missing",
    saved:
      t.server_state === "in_library" ? t.saved_track || undefined : undefined,
    server:
      t.server_state === "household_available"
        ? t.household_track || undefined
        : undefined,
    release_id: release.id,
    track_id: t.id,
    recording_id: t.musicbrainz_recording_id,
    artist_id: artistId,
    explicit_edition: t.explicit_edition,
    recording_kind: t.recording_kind,
  }));
}

// A song Add to library would add: not on the server yet, or a household
// copy someone else already has.
// Not while it is still being looked up (#263).
export const addable = (row: Row) =>
  !row.checking && (row.state === "missing" || row.state === "server");

/** The rows to look up in the library: an explicit edition keeps the
 * release answer's state, since a song matches any edition only unless it
 * is an explicit one. */
export const lookedUp = (plain: Row[]) =>
  plain.filter((r) => !r.explicit_edition);

/** Until the library lookup answers, a song that could be added can't be:
 * one you have on another album would be added again (#263). */
export const checking = (plain: Row[]) =>
  plain.map((r) =>
    r.explicit_edition || !addable(r) ? r : { ...r, checking: true },
  );

/** The rows once the lookup answered: those it found yours become yours. */
export function markYours(plain: Row[], found: Row[]): Row[] {
  const yours = new Map(
    found.filter((s) => s.state === "saved").map((s) => [s.key, s]),
  );
  return plain.map((r) => yours.get(r.key) || r);
}

const songs = (n: number) => `${n} ${n === 1 ? "song" : "songs"}`;
/** The confirmation for an Add to library, from the server's counts; the
 * iPhone words it the same way. */
export function addedMessage(result: AddResult) {
  const adding = result.created + result.existing;
  const parts = [];
  if (adding > 0) parts.push(`Adding ${songs(adding)} to your library.`);
  if (result.skipped > 0) parts.push(`Skipped ${songs(result.skipped)}.`);
  if (result.paused) parts.push(PAUSED_NOTICE);
  return parts.join(" ") || "Already in your library.";
}

/** Why there is nothing to add, once every song's state is known. */
export function nothingToAdd(rows: Row[]) {
  return rows.every((r) => r.state === "saved")
    ? "Already in your library: nothing to add."
    : "Nothing to add: every song is in your library or being added.";
}

/** The mark a catalogue album card shows after its menu looked it up:
 * every song yours, or Add sent and the songs on their way; none while
 * nothing is known or some songs are still to add. */
export function cardMark(rows: Row[] | null): BadgeState | null {
  if (!nothingLeftToAdd(rows)) return null;
  if (rows!.every((r) => r.state === "saved")) return "library";
  // A song you paused shows no "Adding" (#264): it waits for you.
  return rows!.every((r) => r.state === "saved" || r.state === "pending")
    ? "requested"
    : null;
}

/** Known to have nothing Add to library could send, as the page's Add
 * button is off: every song yours, on its way or paused. */
export function nothingLeftToAdd(rows: Row[] | null) {
  return !!rows?.length && !rows.some((r) => addable(r) || r.checking);
}

/**
 * The rows after an Add, from the server's answer for each song sent (the
 * songs `addable` picked, in order): created or existing are on their way,
 * added or in_library are yours, paused stays paused, and excluded is left
 * as it was, so Add can offer it again. Null when the answer doesn't say
 * song by song: the card then reads the album again.
 */
export function afterAdd(rows: Row[], outcomes?: AddOutcome[]): Row[] | null {
  const sent = rows.filter(addable);
  if (!outcomes || outcomes.length !== sent.length) return null;
  const outcome = new Map(sent.map((r, i) => [r.key, outcomes[i]]));
  return rows.map((r) => {
    switch (outcome.get(r.key)) {
      case "created":
      case "existing":
        return { ...r, state: "pending" as const };
      case "added":
      case "in_library":
        return { ...r, state: "saved" as const };
      case "paused":
        return { ...r, state: "paused" as const };
      default:
        return r;
    }
  });
}

/** A release's artwork, as Cover's catalogueCover names it. */
export const releaseArtwork = (id: string) =>
  `/api/v1/catalogue/artwork/release/${encodeURIComponent(id)}`;

/** One of an artist's popular songs, as /popularity lists it. */
export type PopularSong = {
  id: string;
  title: string;
  artist: string;
  album: string;
  release_id: string;
};

/** An artist's popular songs as rows, in the chart's order: the artist
 * page's Popular songs, and what its card plays (#267). */
export function popularRows(artistId: string, songs: PopularSong[]): Row[] {
  return songs.map((song) => ({
    key: "popular:" + song.id,
    title: song.title,
    artist: song.artist,
    album: song.album,
    cover: releaseArtwork(song.release_id),
    state: "missing",
    release_id: song.release_id,
    recording_id: song.id,
    artist_id: artistId,
  }));
}
