import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Play } from "lucide-react";
import { notifyError } from "./notices";
import { chooseReplace } from "./playChoice";
import { usePlayer } from "./Player";
import { withCollection } from "./songActions";
import type { Song } from "./songs";
import "./HomeShelf.css";

/** Home's shelves in page order. While several load, only the first shows
 * its placeholder row: Home never shows more than one (D-120). */
const ORDER = ["mixes", "recommended", "popular"] as const;
type Shelf = (typeof ORDER)[number];

const Loading = createContext<{
  loading: Shelf[];
  set: (shelf: Shelf, loading: boolean) => void;
} | null>(null);

export function ShelfLoading({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState<Shelf[]>([]);
  const set = useCallback(
    (shelf: Shelf, on: boolean) =>
      setLoading((old) =>
        on === old.includes(shelf)
          ? old
          : on
            ? ORDER.filter((s) => s === shelf || old.includes(s))
            : old.filter((s) => s !== shelf),
      ),
    [],
  );
  return (
    <Loading.Provider value={{ loading, set }}>{children}</Loading.Provider>
  );
}

/** Whether this shelf shows the page's one placeholder row. */
export function usePlaceholder(shelf: Shelf, loading: boolean) {
  const context = useContext(Loading);
  const set = context?.set;
  useEffect(() => {
    set?.(shelf, loading);
  }, [set, shelf, loading]);
  useEffect(() => () => set?.(shelf, false), [set, shelf]);
  if (!context) return loading;
  return loading && context.loading[0] === shelf;
}

/** One quiet row of grey tiles where a shelf will be (D-120): no words. */
export function ShelfPlaceholder({ label }: { label: string }) {
  return (
    <section
      className="home-section shelf-placeholder"
      aria-busy="true"
      aria-label={label + ", loading"}
    >
      <span className="shelf-placeholder-heading" />
      <span className="shelf-placeholder-row">
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} />
        ))}
      </span>
    </section>
  );
}

/** Play over a shelf cover's corner; the card itself opens the page. */
export function CoverPlay({
  title,
  songs,
}: {
  title: string;
  songs: () => Promise<Song[]>;
}) {
  const player = usePlayer();
  return (
    <span className="cover-play">
      <button
        type="button"
        aria-label={"Play " + title}
        title={"Play " + title}
        onClick={() => {
          // Plays only if nothing has been played since (Codex, #267).
          const newest = chooseReplace();
          void withCollection(songs, (list) => {
            if (list.length && newest()) player.play(list, 0);
          }).catch((e) => {
            if (newest()) notifyError((e as Error).message);
          });
        }}
      >
        <Play size={16} fill="currentColor" aria-hidden="true" />
      </button>
    </span>
  );
}
