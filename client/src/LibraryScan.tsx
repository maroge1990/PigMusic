import { ScanSearch } from "lucide-react";
import { apiFetch, detailOr, isUnconfirmed, writeFailure } from "./api";
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
      if (!r.ok) throw await writeFailure(r, detailOr("Scan unavailable."));
      setScan(await r.json());
    } catch (e) {
      setError((e as Error).message);
      // It may have started: show the scan as it is now.
      if (isUnconfirmed(e)) window.dispatchEvent(new Event("music-admin-poll"));
    }
  }
  return (
    <section className="system-panel library-tool">
      <h2>
        <ScanSearch size={18} aria-hidden="true" />
        Library scan
      </h2>
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
