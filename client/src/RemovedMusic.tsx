import { apiFetch } from "./api";
import { formatCount, formatDateTime } from "./format";
import { useEffect, useState } from "react";

type Purge = {
  days: number;
  items: {
    id: string;
    title: string;
    artist: string;
    album: string;
    removed_at: number;
    size_bytes: number;
  }[];
  total_bytes: number;
  fingerprint: string;
};

const megabytes = (bytes: number) =>
  formatCount(Math.max(1, Math.round(bytes / 1_000_000))) + " MB";

export function RemovedMusic({ notify }: { notify: (s: string) => void }) {
  const [items, setItems] = useState<
    { id: string; track: { title: string; artist: string }; status: string }[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [days, setDays] = useState(30);
  const [purge, setPurge] = useState<Purge | null>(null);
  const refresh = () =>
    apiFetch("/api/v1/admin/library/trash")
      .then((r) => r.json())
      .then(setItems);
  useEffect(() => {
    void refresh();
  }, []);
  async function restore(id: string) {
    setBusy(true);
    try {
      const r = await apiFetch(
        "/api/v1/admin/library/trash/" + id + "/restore",
        {
          method: "POST",
        },
      );
      if (!r.ok) throw new Error("Restore stopped. Check the library folder.");
      await refresh();
      setPurge(null);
      window.dispatchEvent(new Event("music-library-changed"));
      notify("Song restored.");
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function review() {
    setBusy(true);
    try {
      const r = await apiFetch(
        "/api/v1/admin/library/trash/purge?days=" + days,
      );
      if (!r.ok) throw new Error("Trash can't be read. Check the library.");
      setPurge(await r.json());
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function deleteForGood(plan: Purge) {
    if (
      !confirm(
        `Delete ${plan.items.length} songs from Trash for good? They can't be restored.`,
      )
    )
      return;
    setBusy(true);
    try {
      const r = await apiFetch("/api/v1/admin/library/trash/purge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          days: plan.days,
          fingerprint: plan.fingerprint,
        }),
      });
      if (!r.ok)
        throw new Error("Trash changed since the review. Nothing was deleted.");
      const d: { purged: number; kept: number } = await r.json();
      notify(
        `${d.purged} songs deleted for good` +
          (d.kept ? `; ${d.kept} kept because their file changed.` : "."),
      );
      await refresh();
      setPurge(null);
    } catch (e) {
      notify((e as Error).message);
      setPurge(null);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="system-panel">
      <h2>Removed music</h2>
      {!items.length ? (
        <p>No removed music.</p>
      ) : (
        items.map((item) => (
          <div className="result-row" key={item.id}>
            <div>
              <strong>{item.track.title}</strong>
              <span>{item.track.artist}</span>
            </div>
            <button
              className="secondary-button"
              disabled={busy}
              onClick={() => void restore(item.id)}
            >
              Restore
            </button>
          </div>
        ))
      )}
      <h3>Empty Trash</h3>
      <p>
        Review the songs in Trash longer than a number of days, then delete
        exactly those for good. Nothing is deleted automatically.
      </p>
      <label>
        In Trash longer than (days)
        <input
          type="number"
          min={1}
          max={3650}
          value={days}
          onChange={(e) => {
            setDays(Math.min(3650, Math.max(1, Number(e.target.value) || 1)));
            setPurge(null);
          }}
        />
      </label>
      <button
        className="secondary-button"
        disabled={busy}
        onClick={() => void review()}
      >
        Review
      </button>
      {purge && !purge.items.length && (
        <p>No songs in Trash longer than {purge.days} days.</p>
      )}
      {purge && !!purge.items.length && (
        <>
          {purge.items.map((item) => (
            <div className="result-row" key={item.id}>
              <div>
                <strong>{item.title}</strong>
                <span>
                  {item.artist} · {item.album} · removed{" "}
                  {formatDateTime(item.removed_at)} ·{" "}
                  {megabytes(item.size_bytes)}
                </span>
              </div>
            </div>
          ))}
          <button
            className="secondary-button"
            disabled={busy}
            onClick={() => void deleteForGood(purge)}
          >
            Delete {purge.items.length} for good ·{" "}
            {megabytes(purge.total_bytes)}
          </button>
        </>
      )}
    </section>
  );
}
