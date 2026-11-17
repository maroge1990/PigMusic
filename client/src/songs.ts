import { api, apiFetch, cachedLibrary } from "./api";
import { discoverUrl } from "./navigation";
import type { Track } from "./types";

/** Where a song stands for this person. */
export type SongState = "saved" | "server" | "pending" | "missing";

/** One song row, from any source: your library, the server or the catalogue. */
export type Song = {
  key: string;
  title: string;
  artist: string;
  album?: string;
  album_artist?: string;
  duration?: number;
  year?: string | number;
  track_number?: number;
  disc_number?: number;
  cover?: string | null;
  state: SongState;
  /** A track in this person's library; plays from the server's file. */
  saved?: Track;
  /** Household file not yet in this person's library (adding is instant). */
  server?: Track;
  /** Catalogue identity; streams without downloading when not saved. */
  release_id?: string;
  track_id?: string;
  recording_id?: string;
  /** Chosen from Other versions: only this edition will do. */
  explicit_edition?: boolean;
  /** From the release type: a live, remix or demo recording. */
  recording_kind?: string[];
  /** Catalogue artist, so "Go to artist" skips a name search. */
  artist_id?: string;
  /** Prepared mix entry resolved against the server's validated files. */
  cache_id?: string;
  /** From the person's endless radio: the queue refills as it nears its end. */
  radio?: boolean;
  /** Similar songs that carried on when a queue ended (#202). */
  similar?: boolean;
};

/** A prepared mix or radio entry; plays the library file when there is one. */
export function fromMixTrack(t: Track, radio = false): Song {
  return {
    ...fromTrack({ ...t, id: t.library_track_id || t.id }),
    cache_id: t.id,
    ...(radio ? { radio } : {}),
  };
}

/** A song the server named by id: a mix entry plays as one, a library song
 * as itself. */
export function fromServerTrack(t: Track): Song {
  return t.id.startsWith("mix:") ? fromMixTrack(t) : fromTrack(t);
}

/** A similar song that carries on after a queue ended (#202). */
export function fromSimilarTrack(t: Track): Song {
  return { ...fromServerTrack(t), similar: true };
}

export function fromTrack(t: Track, cover?: string | null, key = t.id): Song {
  const saved =
    t.available !== false &&
    (!t.server_state || t.server_state === "in_library");
  return {
    key,
    title: t.title,
    artist: t.artist,
    album: t.album,
    album_artist: t.album_artist,
    duration: t.duration,
    year: t.year,
    track_number: t.track_number,
    disc_number: t.disc_number,
    cover: t.cover_url || cover,
    state:
      t.server_state === "household_available"
        ? "server"
        : t.server_state === "requested"
          ? "pending"
          : saved
            ? "saved"
            : "missing",
    saved: saved ? t : undefined,
    server: t.server_state === "household_available" ? t : undefined,
    release_id: t.musicbrainz_release_id,
    recording_id: t.musicbrainz_recording_id,
  };
}

export function playlistSongs(
  entries: (Track & {
    entry_id: string;
    saved_track?: Track | null;
    household_track?: Track | null;
  })[],
  cover?: string | null,
): Song[] {
  return entries.map((entry) => {
    // Your file, else the household file, which plays without being added.
    const song = fromTrack(
      entry.saved_track || entry.household_track || entry,
      cover,
      entry.entry_id,
    );
    return entry.saved_track || song.state === "server"
      ? song
      : { ...song, state: "missing", saved: undefined };
  });
}

/** The fields the server needs to request, favourite or list a song. */
export function songBody(s: Song) {
  const t = s.saved || s.server;
  return {
    title: s.title,
    artist: s.artist,
    album: s.album || "",
    album_artist: s.album_artist || t?.album_artist || s.artist,
    duration: s.duration || 0,
    track_number: s.track_number || 1,
    disc_number: s.disc_number || 1,
    year: String(s.year || t?.year || ""),
    musicbrainz_release_id: s.release_id,
    musicbrainz_recording_id: s.recording_id,
    explicit_edition: !!s.explicit_edition,
    recording_kind: s.recording_kind || [],
  };
}

/** What the server did with an Add to library: new jobs, songs already
 * on their way or now in your library, and songs it refused. */
export type AddResult = { created: number; existing: number; skipped: number };

export async function requestSong(s: Song): Promise<AddResult> {
  const result = await api<AddResult>("/v1/requests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tracks: [songBody(s)] }),
  });
  window.dispatchEvent(new Event("music-library-changed"));
  window.dispatchEvent(new Event("music-requests-changed"));
  return result;
}

/** Mark catalogue songs that are already in this person's library. */
export async function withLibrary(songs: Song[]): Promise<Song[]> {
  const open = songs.filter((s) => s.state !== "saved");
  if (!open.length) return songs;
  const found = await api<{ tracks: (Track | null)[] }>("/v1/library/lookup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      songs: open.slice(0, 200).map((s) => ({
        title: s.title,
        artist: s.artist,
        musicbrainz_recording_id: s.recording_id,
        duration: s.duration || 0,
        recording_kind: s.recording_kind || [],
      })),
    }),
  });
  const saved = new Map(open.map((s, i) => [s.key, found.tracks[i]]));
  return songs.map((s) => {
    const track = saved.get(s.key);
    return track
      ? {
          ...s,
          state: "saved",
          saved: track,
          cover: s.cover || track.cover_url,
        }
      : s;
  });
}

type LibraryIndex = {
  artists: { id: string; name: string }[];
  albums: {
    id: string;
    title: string;
    artist: string;
    musicbrainz_release_id?: string;
  }[];
};
const same = (a: string, b: string) =>
  a.toLocaleLowerCase() === b.toLocaleLowerCase();

/** Open the full artist catalogue, preserving a known catalogue identity. */
export async function goToArtist(name: string, catalogueId?: string) {
  if (catalogueId) {
    location.hash = discoverUrl({ id: catalogueId, name });
    return;
  }
  const exact = await catalogueArtist(name);
  location.hash = exact
    ? discoverUrl(exact)
    : "/search?q=" + encodeURIComponent(name);
}

/** The playing song's artist, where playback started: a library song opens
 * the library artist page, a catalogue song the full artist page. */
export async function goToPlayingArtist(s: Song) {
  if (!s.artist_id && s.saved) {
    const library = await cachedLibrary<LibraryIndex>();
    const names = [s.artist, s.album_artist || s.saved.album_artist];
    for (const name of names) {
      const found = name && library.artists.filter((a) => same(a.name, name));
      if (found && found.length === 1) {
        location.hash = `/library/artist/${found[0].id}`;
        return;
      }
    }
  }
  await goToArtist(s.artist, s.artist_id);
}

export async function goToAlbum(
  album: string,
  artist: string,
  releaseId?: string,
  catalogueArtistId?: string,
) {
  const library = await cachedLibrary<LibraryIndex>();
  const mine = library.albums.filter(
    (a) =>
      same(a.title, album) &&
      same(a.artist, artist) &&
      (!releaseId ||
        !a.musicbrainz_release_id ||
        a.musicbrainz_release_id === releaseId),
  );
  if (mine.length === 1) {
    location.hash = `/library/album/${mine[0].id}`;
    return;
  }
  const exact = catalogueArtistId
    ? { id: catalogueArtistId, name: artist }
    : await catalogueArtist(artist);
  location.hash = exact
    ? discoverUrl(exact, releaseId)
    : "/search?q=" + encodeURIComponent(album);
}

async function catalogueArtist(name: string) {
  const results = await apiFetch(
    "/api/v1/catalogue/artists?q=" + encodeURIComponent(name),
  ).then((r) => r.json());
  const exact = (results.artists || []).filter((a: { name: string }) =>
    same(a.name, name),
  );
  return exact.length === 1 ? (exact[0] as { id: string; name: string }) : null;
}
