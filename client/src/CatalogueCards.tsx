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
  // What the latest lookup or Add found, for the session it was found in.
  const [known, setKnown] = useState<{
    session: number;
    rows: Song[];
  } | null>(null);
  const knownNow = useRef(known);
  knownNow.current = known;
  // Each read or Add is numbered as it starts; an answer is kept only if
  // nothing that started later has been kept, so an older lookup never
  // undoes a newer state.
  const order = useRef({ started: 0, applied: 0 });
  const begin = () => ({
    stamp: ++order.current.started,
    at: currentSession(),
  });
  const keep = (from: { stamp: number; at: number }, rows: Song[] | null) => {
    if (currentSession() !== from.at || from.stamp <= order.current.applied)
      return;
    order.current.applied = from.stamp;
    setKnown(rows && { session: from.at, rows });
  };
  // Once the card knows where its songs stand, it reads them again, as the
  // page does, whenever your library or your requests change: a song
  // cancelled, finished or excluded shows as it now is (Codex round 1).
  useEffect(() => {
    let active = true;
    let running = false;
    let again = false;
    const reread = () => {
      if (!knownNow.current) return;
      if (running) {
        again = true;
        return;
      }
      running = true;
      const read = begin();
      void catalogueAlbumSongs(group, artistId)
        .then((rows) => active && keep(read, rows))
        .catch(() => active && keep(read, null))
        .finally(() => {
          running = false;
          if (again && active) {
            again = false;
            reread();
          }
        });
    };
    window.addEventListener("music-library-changed", reread);
    window.addEventListener("music-requests-changed", reread);
    return () => {
      active = false;
      window.removeEventListener("music-library-changed", reread);
      window.removeEventListener("music-requests-changed", reread);
    };
  }, [group, artistId]);
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
