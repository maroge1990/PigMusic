import { useEffect, useRef, useState, type ReactNode } from "react";
import { currentSession } from "./api";
import { cardMark, nothingLeftToAdd } from "./catalogueAlbum";
import {
  addCatalogueAlbum,
  cardFailure,
  catalogueAlbumSongs,
  catalogueArtistSongs,
} from "./catalogueActions";
import { useCollectionMenu } from "./CollectionMenu";
import { StateBadge } from "./StateBadge";
import type { Song } from "./songs";
import { currentQueue, queueSignature, type QueueItem } from "./queueFeed";

/** How long a card waits after its songs' requests change before it reads
 * its album again: a burst of changes costs one read. */
const REFRESH_QUIET_MS = 500;

type Known = { session: number; rows: Song[]; basis: string };
type Read = { stamp: number; at: number; queue: QueueItem[] | null };

/**
 * A card for an album not (or not all) in your library (D-129, #267): the
 * one collection menu of D-118, as on a library album's card. Its songs are
 * the edition the album's page opens on, looked up when an item needs them;
 * Add to library sends what the page's Add would send. Once a lookup or an
 * Add has told where its songs stand, the card shows the mark: "In your
 * library", or "Adding" with the clock.
 */
export function CatalogueAlbumCard({
  group,
  title,
  artistId,
  children,
}: {
  /** The release group the card opens. */
  group: string;
  title: string;
  /** The catalogue artist the album's page belongs to. */
  artistId: string;
  /** The card itself, given the mark to show once there is one. */
  children: (mark: ReactNode) => ReactNode;
}) {
  // What the latest lookup or Add found, for the session it was found in,
  // and where its songs' requests stood in the change feed when that read
  // started (`basis`).
  const [known, setKnownState] = useState<Known | null>(null);
  const knownNow = useRef<Known | null>(null);
  const setKnown = (value: Known | null) => {
    knownNow.current = value;
    setKnownState(value);
  };
  const alive = useRef(true);
  // Each read or Add is numbered as it starts; an answer is kept only if
  // nothing that started later has been kept, so an older lookup never
  // undoes a newer state.
  const order = useRef({ started: 0, applied: 0 });
  const refresh = useRef<{
    timer?: ReturnType<typeof setTimeout>;
    running: boolean;
    again: boolean;
  }>({ running: false, again: false });
  const begin = (): Read => ({
    stamp: ++order.current.started,
    at: currentSession(),
    queue: currentQueue(),
  });
  const keep = (from: Read, rows: Song[] | null) => {
    if (
      !alive.current ||
      currentSession() !== from.at ||
      from.stamp <= order.current.applied
    )
      return;
    order.current.applied = from.stamp;
    setKnown(
      rows && {
        session: from.at,
        rows,
        basis: queueSignature(rows, from.queue),
      },
    );
    // A change to these songs that came while this was read (Codex round
    // 2): read once more, rather than keep an answer it may have missed.
    check();
  };
  /** Reads the album again, after a quiet moment, only if one of its own
   * songs' requests changed state since what the card shows was read:
   * download progress and other songs change nothing (Codex round 2). */
  const check = () => {
    const now = knownNow.current;
    if (
      !now ||
      now.session !== currentSession() ||
      queueSignature(now.rows, currentQueue()) === now.basis
    )
      return;
    clearTimeout(refresh.current.timer);
    refresh.current.timer = setTimeout(reread, REFRESH_QUIET_MS);
  };
  const reread = () => {
    const state = refresh.current;
    if (!alive.current || !knownNow.current) return;
    if (state.running) {
      state.again = true;
      return;
    }
    state.running = true;
    const read = begin();
    void catalogueAlbumSongs(group, artistId)
      .then((rows) => keep(read, rows))
      .catch(() => keep(read, null))
      .finally(() => {
        state.running = false;
        if (state.again) {
          state.again = false;
          check();
        }
      });
  };
  useEffect(() => {
    alive.current = true;
    const changed = () => check();
    window.addEventListener("music-queue", changed);
    return () => {
      alive.current = false;
      clearTimeout(refresh.current.timer);
      window.removeEventListener("music-queue", changed);
    };
  }, []);
  const rows = known && known.session === currentSession() ? known.rows : null;
  const mark = cardMark(rows);
  const { menu, parts } = useCollectionMenu({
    kind: "album",
    title,
    songs: async () => {
      const read = begin();
      const list = await catalogueAlbumSongs(group, artistId).catch((e) => {
        throw cardFailure(e, title);
      });
      keep(read, list);
      return list;
    },
    addToLibrary: async (list, session, again) => {
      // Numbered before it is sent: a read started after it is newer.
      const add = begin();
      const now = await addCatalogueAlbum(list, session, again);
      if (now) keep(add, now);
    },
    // As on the page: no Add once nothing is left to add.
    cannotAdd: nothingLeftToAdd(rows),
  });
  return (
    <div className="collection-card" {...menu.target}>
      {children(mark && <StateBadge state={mark} iconOnly />)}
      {parts}
    </div>
  );
}

/**
 * A card for a catalogue artist (D-129, #267): the one collection menu, its
 * songs the artist's popular songs, as the artist's page lists them. The
 * artist's page has no Add to library, so neither has the card.
 */
export function CatalogueArtistCard({
  artistId,
  name,
  children,
}: {
  /** The catalogue artist the card opens. */
  artistId: string;
  name: string;
  children: ReactNode;
}) {
  const { menu, parts } = useCollectionMenu({
    kind: "artist",
    title: name,
    songs: () =>
      catalogueArtistSongs(artistId).catch((e) => {
        throw cardFailure(e, name);
      }),
    canAddToLibrary: false,
  });
  return (
    <div className="collection-card" {...menu.target}>
      {children}
      {parts}
    </div>
  );
}
