import { apiFetch } from "./api";
import { useEffect, useRef, useState } from "react";
export function ArtistAcquisition({
  id,
  notify,
}: {
  id: string;
  notify: (s: string) => void;
}) {
  const editing = useRef(false);
  const [state, setState] = useState<{
    kinds: string[];
    monitor: boolean;
    status: string;
    created?: number;
    done?: string[];
    total?: number;
  }>({ kinds: ["album"], monitor: false, status: "idle" });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    const update = () =>
      apiFetch(`/api/v1/admin/catalogue/artists/${id}/acquisition`)
        // A failed answer keeps the current settings rather than replacing them.
        .then((r) =>
          r.ok ? r.json() : Promise.reject(new Error(r.statusText)),
        )
        .then((d) => {
          if (active)
            setState((old) =>
              editing.current
                ? {
                    ...old,
                    status: d.status,
                    created: d.created,
                    done: d.done,
                    total: d.total,
                  }
                : d,
            );
        })
        .catch(() => {});
    void update();
    // A job that starts by itself (a monitored artist's new release) queues
    // requests, which the event stream reports; poll only while one runs.
    const changed = () => void update();
    window.addEventListener("music-requests-changed", changed);
    const timer = ["queued", "running"].includes(state.status)
      ? setInterval(() => {
          if (!document.hidden) void update();
        }, 5000)
      : undefined;
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener("music-requests-changed", changed);
    };
  }, [id, state.status]);
  async function save(request = false) {
    setBusy(true);
    try {
      const r = await apiFetch(
        `/api/v1/admin/catalogue/artists/${id}/acquisition`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            kinds: state.kinds,
            monitor: state.monitor,
            request,
          }),
        },
      );
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Artist request unavailable.");
      editing.current = false;
      setState(d);
      notify(request ? "Artist request queued." : "Artist settings saved.");
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="album-quality">
      <summary>Artist download settings</summary>
      {["album", "single", "ep"].map((kind) => (
        <label className="download-option" key={kind}>
          <input
            type="checkbox"
            checked={state.kinds.includes(kind)}
            onChange={(e) => {
              editing.current = true;
              setState({
                ...state,
                kinds: e.target.checked
                  ? [...state.kinds, kind]
                  : state.kinds.filter((k) => k !== kind),
              });
            }}
          />
          {kind === "album" ? "Albums" : kind === "single" ? "Singles" : "EPs"}
        </label>
      ))}
      <label className="download-option">
        <input
          type="checkbox"
          checked={state.monitor}
          onChange={(e) => {
            editing.current = true;
            setState({ ...state, monitor: e.target.checked });
          }}
        />
        Monitor new releases
      </label>
      <div className="release-actions">
        <button
          className="secondary-button"
          disabled={busy || !state.kinds.length}
          onClick={() => void save()}
        >
          Save settings
        </button>
        <button
          className="primary-button"
          disabled={
            busy ||
            !state.kinds.length ||
            ["queued", "running"].includes(state.status)
          }
          onClick={() => void save(true)}
        >
          Download artist
        </button>
      </div>
      {state.status !== "idle" && (
        <p>
          {state.status} · {state.done?.length || 0} of {state.total || 0}{" "}
          releases · {state.created || 0} songs queued
        </p>
      )}
    </details>
  );
}
