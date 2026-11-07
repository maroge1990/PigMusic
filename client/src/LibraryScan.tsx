import { apiFetch } from "./api";
import { useEffect, useState } from "react";
import { formatCount, formatDateTime } from "./format";
import { InlineError } from "./EmptyState";
type Scan = {
  status?: string;
  files?: number;
  missing?: number;
  read_failures?: number;
  completed_at?: string;
  error?: string;
};
export function LibraryScan({ refresh }: { refresh: () => void }) {
  const [scan, setScan] = useState<Scan>({});
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let previous = "";
    const update = () =>
      apiFetch("/api/v1/admin/library/scan")
        .then((r) => r.json())
        .then((v) => {
          if (!active) return;
          setScan(v);
          if (previous === "scanning" && v.status === "completed") refresh();
          previous = v.status;
        })
        .catch(() => {});
    // Progress arrives with the shell's owner `admin` event; no timer here.
    const pushed = (event: Event) => {
      const value = (event as CustomEvent<{ scan?: Scan }>).detail?.scan;
      if (!active || !value) return;
      setScan(value);
      if (previous === "scanning" && value.status === "completed") refresh();
      previous = value.status ?? "";
    };
    const poll = () => void update();
    void update();
    window.addEventListener("music-admin-state", pushed);
    window.addEventListener("music-admin-poll", poll);
    return () => {
      active = false;
      window.removeEventListener("music-admin-state", pushed);
      window.removeEventListener("music-admin-poll", poll);
    };
  }, []);
  async function start() {
    setError("");
    try {
      const r = await apiFetch("/api/v1/admin/library/scan", {
        method: "POST",
      });
      const value = await r.json();
      if (!r.ok) throw new Error(value.detail || "Scan unavailable.");
      setScan(value);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <section className="system-panel">
      <h2>Library scan</h2>
      <p>
        Read tags and artwork from your configured music folder. Existing files
        are left unchanged.
      </p>
      <button
        className="secondary-button"
        disabled={scan.status === "scanning"}
        onClick={() => void start()}
      >
        {scan.status === "scanning"
          ? `Scanning · ${scan.files || 0} files`
          : "Scan library"}
      </button>
      {scan.status === "completed" && (
        <p>
          Last scan: {formatCount(scan.files)} files
          {scan.completed_at ? ` · ${formatDateTime(scan.completed_at)}` : ""}
          {scan.missing ? ` · ${scan.missing} unavailable` : ""}
          {scan.read_failures ? ` · ${scan.read_failures} need attention` : ""}
        </p>
      )}
      {(error || scan.error) && (
        <InlineError>{error || scan.error}</InlineError>
      )}
    </section>
  );
}
