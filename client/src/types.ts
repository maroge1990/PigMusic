export type Track = {
  server_state?:
    | "not_in_library"
    | "household_available"
    | "requested"
    | "in_library"
    | "failed";
  id: string;
  library_track_id?: string | null;
  title: string;
  artist: string;
  album: string;
  album_artist: string;
  track_number: number;
  disc_number: number;
  duration: number;
  year?: string | number;
  musicbrainz_release_id?: string;
  musicbrainz_recording_id?: string;
  codec?: string;
  bitrate?: number;
  relative_path?: string;
  album_id?: string;
  cover_url?: string | null;
  available?: boolean;
  status?: "downloaded" | "missing" | "review";
  reason?: string;
  selected?: boolean;
  decision?: "requested" | "skip";
};
export type Album = {
  id: string;
  title: string;
  artist: string;
  artist_id?: string;
  year: number;
  compilation: boolean;
  track_count: number;
  tracks?: Track[];
  codecs?: string[];
  category: "album" | "compilation" | "soundtrack" | "musical";
  cover_url?: string | null;
  saved_count?: number;
  selected_count?: number;
  /** When its newest song joined your library (ISO), if known. */
  added_at?: string | null;
};
export type Artist = {
  id: string;
  name: string;
  album_ids: string[];
  album_count: number;
  track_count: number;
  compilation_only: boolean;
  cover_url?: string | null;
  albums?: Album[];
  compilation_tracks?: Track[];
};
export type Library = {
  media?: {
    portraits: boolean;
    fanart_configured: boolean;
    remote_playback: boolean;
    listenbrainz_configured: boolean;
  };
  artists: Artist[];
  albums: Album[];
  playlist_count?: number;
  /** Changes whenever this person's library does. */
  revision?: string;
  stats: {
    tracks: number;
    artists: number;
    albums: number;
    compilations: number;
    soundtracks: number;
    musicals: number;
  };
  snapshot: {
    source: string;
    captured_at: string;
    read_failures: { path: string; error: string }[];
  };
};
export type Migration = {
  source_name: string;
  imported_at: string;
  albums: Album[];
  stats: {
    tracks: number;
    saved: number;
    missing: number;
    review: number;
    selected: number;
  };
};
