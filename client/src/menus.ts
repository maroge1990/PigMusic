/**
 * What the one song menu and the one collection menu hold, in order (D-118,
 * #248, #249). The components draw these lists; nothing else decides order.
 */

export type SongMenuItem =
  | "play-next"
  | "add-to-queue"
  | "add-to-library"
  | "favourite"
  | "add-to-playlist"
  | "download"
  | "go-to-album"
  | "go-to-artist"
  | "move-up"
  | "remove-from-playlist"
  | "hide-from-mixes"
  | "remove-from-up-next"
  | "remove-from-library";

/** The page a song row is on; it adds the page's own items. */
export type SongPage =
  | "library"
  | "playlist"
  | "queue"
  | "search"
  | "mix"
  | "chart"
  | "rewind";

export type SongMenuFacts = {
  page: SongPage;
  /** Where the song stands for this person. */
  state: "saved" | "server" | "pending" | "paused" | "missing";
  /** The server holds a file for it (yours, the household's or a mix's). */
  hasFile: boolean;
  /** It names an album, and the row is not already on that album's page. */
  album: boolean;
  /** The row is not already on its artist's page. */
  artist: boolean;
  /** A playlist the person may reorder (not Favourites). */
  editablePlaylist?: boolean;
};

/** The song menu as groups of items, in D-118's order. */
export function songMenu(facts: SongMenuFacts): SongMenuItem[][] {
  const own: SongMenuItem[] =
    facts.page === "playlist" && facts.editablePlaylist
      ? ["move-up", "remove-from-playlist"]
      : facts.page === "queue"
        ? ["remove-from-up-next"]
        : facts.page === "mix"
          ? ["hide-from-mixes"]
          : [];
  const groups: SongMenuItem[][] = [
    ["play-next", "add-to-queue"],
    [
      ...(facts.state === "missing" || facts.state === "server"
        ? (["add-to-library"] as const)
        : []),
      "favourite",
      "add-to-playlist",
    ],
    facts.hasFile ? ["download"] : [],
    [
      ...(facts.album ? (["go-to-album"] as const) : []),
      ...(facts.artist ? (["go-to-artist"] as const) : []),
    ],
    own,
    facts.state === "saved" ? ["remove-from-library"] : [],
  ];
  return groups.filter((group) => group.length);
}

export type CollectionKind = "album" | "artist" | "playlist" | "mix";

export type CollectionMenuItem =
  | "play"
  | "shuffle"
  | "play-next"
  | "add-to-library"
  | "add-to-playlist"
  | "download"
  | "save-mix"
  | "hide-edition";

/** The collection menu for an album, artist, playlist or mix. Save and Hide
 * this edition live only in a mix's menu. */
export function collectionMenu(kind: CollectionKind): CollectionMenuItem[][] {
  const groups: CollectionMenuItem[][] = [
    ["play", "shuffle", "play-next"],
    ["add-to-library", "add-to-playlist", "download"],
  ];
  if (kind === "mix") groups.push(["save-mix", "hide-edition"]);
  return groups;
}

/** A collection menu's kinds of action (#267). Play, Shuffle and Play next
 * are one kind: a later one replaces an earlier one still reading its
 * songs, so only the last choice plays. Add to library and Download can't
 * be taken back once sent: while one is on its way, choosing it again does
 * nothing, so a double click acts once. */
export type ActionSlot = "play" | "add" | "download";
export type Choice = { slot: ActionSlot; n: number; done: boolean };

/** Records a new choice of this kind, or null when it is to be ignored. */
export function latestChoice(
  latest: Map<ActionSlot, Choice>,
  slot: ActionSlot,
): Choice | null {
  const before = latest.get(slot);
  if (slot !== "play" && before && !before.done) return null;
  const choice = { slot, n: (before?.n ?? 0) + 1, done: false };
  latest.set(slot, choice);
  return choice;
}

/** Whether no later choice of the same kind has replaced this one. */
export const isLatest = (latest: Map<ActionSlot, Choice>, choice: Choice) =>
  latest.get(choice.slot) === choice;

/** Words for each item; the menus show exactly these. */
export const songMenuLabels: Record<SongMenuItem, string> = {
  "play-next": "Play next",
  "add-to-queue": "Add to queue",
  "add-to-library": "Add to library",
  favourite: "Favourite",
  "add-to-playlist": "Add to playlist",
  download: "Download",
  "go-to-album": "Go to album",
  "go-to-artist": "Go to artist",
  "move-up": "Move up",
  "remove-from-playlist": "Remove from playlist",
  "hide-from-mixes": "Hide from mixes",
  "remove-from-up-next": "Remove from Up next",
  "remove-from-library": "Remove from library",
};

export const collectionMenuLabels: Record<CollectionMenuItem, string> = {
  play: "Play",
  shuffle: "Shuffle",
  "play-next": "Play next",
  "add-to-library": "Add to library",
  "add-to-playlist": "Add to playlist",
  download: "Download",
  "save-mix": "Save this edition",
  "hide-edition": "Hide this edition",
};

/** A tap plays the song and carries on through its list; in search it plays
 * only that song (D-118). */
export function tapQueue<T extends object>(
  page: SongPage,
  list: T[],
  index: number,
) {
  return page === "search"
    ? { queue: [{ ...list[index], search_play: true }], index: 0 }
    : { queue: list, index };
}
