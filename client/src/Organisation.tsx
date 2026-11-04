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

export function Organisation({ notify }: { notify: (s: string) => void }) {
  const [history, setHistory] = useState<{ id: string; title: string }[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const watching = useRef(false);
  useEffect(() => {
    void apiFetch("/api/v1/admin/library/organisation/history")
      .then((r) => r.json())
      .then(setHistory);
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
    setHistory(
      await apiFetch("/api/v1/admin/library/organisation/history").then((r) =>
        r.json(),
      ),
    );
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
    }[]
  >([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [tags, setTags] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function review() {
    setBusy(true);
    try {
      const r = await apiFetch("/api/v1/admin/library/organisation");
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
              onChange={(e) => setTags(e.target.checked)}
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
                      !i.conflict && (i.rename || (tags && i.tag_supported)),
                  )
                  .map((i) => i.id),
              )
            }
          >
            Select eligible changes
          </button>
          <div className="organisation-list">
            {items
              .filter((i) => i.rename || tags)
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
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
