import { apiFetch } from "./api";
import { useEffect, useRef, useState } from "react";

type Job = {
  status: "idle" | "running" | "stopping" | "completed" | "stopped";
  total?: number;
  done?: number;
  message?: string | null;
};
const JOB = "/api/v1/admin/library/organisation/job";
const active = (job: Job | null) =>
  job?.status === "running" || job?.status === "stopping";
const HISTORY = "/api/v1/admin/library/organisation/history";
// The Undo list shows the newest operations a page at a time (#119).
const PAGE = 50;
type Undoable = { id: string; title: string };

export function Organisation({ notify }: { notify: (s: string) => void }) {
  const [history, setHistory] = useState<Undoable[]>([]);
  const [more, setMore] = useState(false);
  async function loadHistory(offset: number) {
    // One extra row says whether there is another page.
    const page: Undoable[] = await apiFetch(
      `${HISTORY}?limit=${PAGE + 1}&offset=${offset}`,
    ).then((r) => r.json());
    setMore(page.length > PAGE);
    setHistory((old) => {
      const kept = offset ? old : [];
      const known = new Set(kept.map((v) => v.id));
      return [...kept, ...page.slice(0, PAGE).filter((v) => !known.has(v.id))];
    });
  }
  const [job, setJob] = useState<Job | null>(null);
  const watching = useRef(false);
  useEffect(() => {
    void loadHistory(0);
    // A job started earlier (or from another device) keeps showing its progress.
    void apiFetch(JOB)
      .then((r) => (r.ok ? r.json() : null))
      .then((j: Job | null) => {
        if (active(j)) {
          watching.current = true;
          setJob(j);
        }
      })
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!active(job)) return;
    // The server applies an album at a time in the background (#93).
    const timer = window.setTimeout(() => {
      void apiFetch(JOB)
        .then((r) => r.json())
        .then((next: Job) => {
          setJob(next);
          if (!active(next) && watching.current) {
            watching.current = false;
            void finished(next);
          }
        })
        .catch(() => setJob((old) => (old ? { ...old } : old)));
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [job]);
  async function finished(result: Job) {
    if (result.status === "completed")
      notify(`${result.done ?? 0} songs organised.`);
    else setError(result.message || "Organisation stopped.");
    window.dispatchEvent(new Event("music-library-changed"));
    await loadHistory(0);
    setItems([]);
    setSelected([]);
  }
  const [items, setItems] = useState<
    {
      id: string;
      from: string;
      to: string;
      rename: boolean;
      conflict: boolean;
      tag_supported: boolean;
      tags_differ: boolean | null;
    }[]
  >([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [tags, setTags] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // With tags, the server reads each file's tags to list only songs whose
  // tags differ from the library metadata (#119).
  async function review(withTags = tags) {
    setBusy(true);
    setError("");
    try {
      const r = await apiFetch(
        "/api/v1/admin/library/organisation" + (withTags ? "?tags=true" : ""),
      );
      const d = await r.json();
      if (!r.ok) throw new Error("Library review unavailable.");
      setItems(d.items);
      setSelected([]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (
      !confirm(
        `Apply the reviewed naming${tags ? " and tagging" : ""} changes to ${selected.length} songs? Originals will be preserved.`,
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      const r = await apiFetch("/api/v1/admin/library/organisation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_ids: selected, tags }),
      });
      const d = await r.json();
      if (!r.ok)
        throw new Error(
          typeof d.detail === "string" ? d.detail : "Organisation stopped.",
        );
      watching.current = true;
      setJob(d);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="system-panel">
      <h2>Organise existing music</h2>
      <p>
        Review naming changes before applying them. Album folders are retained.
        Originals are preserved.
      </p>
      <button
        className="secondary-button"
        disabled={busy || active(job)}
        onClick={() => void review()}
      >
        Review library
      </button>
      {active(job) && (
        <div className="result-row" role="status">
          <strong>
            {job?.status === "stopping"
              ? "Stopping after this album…"
              : `Organising ${job?.done ?? 0} of ${job?.total ?? 0} songs…`}
          </strong>
          <button
            className="secondary-button"
            disabled={job?.status === "stopping"}
            onClick={() =>
              void apiFetch(JOB + "/stop", { method: "POST" })
                .then((r) => r.json())
                .then(setJob)
            }
          >
            Stop
          </button>
        </div>
      )}
      {!!items.length && !active(job) && (
        <>
          <label className="download-option">
            <input
              type="checkbox"
              checked={tags}
              disabled={busy}
              onChange={(e) => {
                setTags(e.target.checked);
                if (e.target.checked) void review(true);
              }}
            />
            Apply consistent tags from current library metadata
          </label>
          <button
            className="text-button"
            onClick={() =>
              setSelected(
                items
                  .filter(
                    (i) =>
                      !i.conflict &&
                      (i.rename ||
                        (tags && i.tag_supported && !!i.tags_differ)),
                  )
                  .map((i) => i.id),
              )
            }
          >
            Select eligible changes
          </button>
          {!items.some((i) => i.rename || (tags && i.tags_differ)) && (
            <p>
              {tags
                ? "Every song is named and tagged consistently."
                : "Every song is named consistently."}
            </p>
          )}
          <div className="organisation-list">
            {items
              .filter((i) => i.rename || (tags && i.tags_differ))
              .map((i) => (
                <label className="result-row" key={i.id}>
                  <input
                    type="checkbox"
                    disabled={busy || i.conflict || (tags && !i.tag_supported)}
                    checked={selected.includes(i.id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, i.id]
                          : selected.filter((id) => id !== i.id),
                      )
                    }
                  />
                  <div>
                    <strong>{i.from}</strong>
                    <span>
                      {i.conflict ? "Destination needs review" : i.to}
                    </span>
                  </div>
                </label>
              ))}
          </div>
          <button
            className="primary-button"
            disabled={busy || !selected.length}
            onClick={() => void apply()}
          >
            Apply reviewed changes · {selected.length}
          </button>
        </>
      )}
      {history.map((item) => (
        <div className="result-row" key={item.id}>
          <strong>{item.title}</strong>
          <button
            className="secondary-button"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void apiFetch(
                "/api/v1/admin/library/organisation/" + item.id + "/undo",
                {
                  method: "POST",
                },
              )
                .then((r) => {
                  if (!r.ok)
                    throw new Error("Undo stopped; original retained.");
                  setHistory((old) => old.filter((v) => v.id !== item.id));
                  window.dispatchEvent(new Event("music-library-changed"));
                })
                .catch((e) => setError(e.message))
                .finally(() => setBusy(false));
            }}
          >
            Undo organisation
          </button>
        </div>
      ))}
      {more && (
        <button
          className="text-button"
          disabled={busy}
          onClick={() => void loadHistory(history.length)}
        >
          Show more
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
