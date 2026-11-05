import type { Track } from "./types";

/** Playlists as the library pages read them. */
export type Entry = Track & {
  entry_id: string;
  position: number;
  saved_track?: Track | null;
  household_available?: boolean;
  household_track?: Track | null;
};
export type Playlist = {
  id: string;
  name: string;
  revision: number;
  favourites?: boolean;
  entries: Entry[];
};
export type PlaylistSummary = {
  id: string;
  name: string;
  track_count?: number;
  available_count?: number;
};
