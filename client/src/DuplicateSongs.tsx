import { apiFetch } from "./api";
import { restoreFromTrash } from "./trashUndo";
import { offerUndo } from "./UndoBar";
import { useState } from "react";
import { EmptyLine, InlineError } from "./EmptyState";

type Song = { id: string; title: string; artist: string };
type Duplicate = {
  track: Song;
  album: string;
  keeper: Song;
  keeper_album: string;
};

/** Downloaded songs the library already had on another album. */
export function DuplicateSongs({ notify }: { notify: (s: string) => void }) {
  const [items, setItems] = useState<Duplicate[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function review() {
    setBusy(true);
    setError("");
    try {
      const r = await apiFetch("/api/v1/admin/library/duplicates");
      if (!r.ok) throw new Error("Duplicate review unavailable.");
      const d: { duplicates: Duplicate[] } = await r.json();
      setItems(d.duplicates);
      setSelected(d.duplicates.map((i) => i.track.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    // Acts at once; Undo puts them back (D-79).
    setBusy(true);
    try {
      const r = await apiFetch("/api/v1/admin/library/duplicates/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_ids: selected }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error("Removal stopped. Check the library folder.");
      const message =
        `${d.removed} duplicates moved to Removed music` +
        (d.skipped ? `; ${d.skipped} left as they were.` : ".");
      notify(message);
      if (d.restorable?.length)
        offerUndo(
          message,
          async () => {
            await restoreFromTrash(d.restorable);
            await review();
          },
          `${d.restorable.length} duplicates put back.`,
        );
      window.dispatchEvent(new Event("music-library-changed"));
      await review();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const albums = new Map<string, Duplicate[]>();
  for (const item of items || []) {
    const key = item.track.artist + " · " + item.album;
    albums.set(key, [...(albums.get(key) || []), item]);
  }
  return (
    <section className="system-panel">
      <h2>Duplicate songs</h2>
      <p>
        Downloaded songs the library already had on another album. Your own
        files and editions chosen from Other versions are never listed.
      </p>
      <button
        className="secondary-button"
        disabled={busy}
        onClick={() => void review()}
      >
        Find duplicates
      </button>
      {items && !items.length && <EmptyLine>No duplicate songs.</EmptyLine>}
      {!!items?.length && (
        <>
          <div className="organisation-list">
            {[...albums].map(([album, songs]) => (
              <div key={album}>
                <h3>{album}</h3>
                {songs.map((i) => (
                  <label className="result-row" key={i.track.id}>
                    <input
                      type="checkbox"
                      disabled={busy}
                      checked={selected.includes(i.track.id)}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...selected, i.track.id]
                            : selected.filter((id) => id !== i.track.id),
                        )
                      }
                    />
                    <div>
                      <strong>{i.track.title}</strong>
                      <span>Kept on {i.keeper_album}</span>
                    </div>
                  </label>
                ))}
              </div>
            ))}
          </div>
          <button
            className="primary-button"
            disabled={busy || !selected.length}
            onClick={() => void remove()}
          >
            Move to Removed music · {selected.length}
          </button>
        </>
      )}
      {error && <InlineError>{error}</InlineError>}
    </section>
  );
}
