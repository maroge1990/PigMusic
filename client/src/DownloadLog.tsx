import { useState } from "react";
import { Check, X } from "lucide-react";
import { api } from "./api";

type Line = { text: string; ok: boolean; seconds?: number | null };
type Log = { id: string; status: string; lines: Line[] };

/** One download told step by step (#197): what was searched, the copies
 * found, each one tried and why it was refused, the checks, any conversion
 * and where the file went. Loaded the first time it is opened. */
export function DownloadLog({ path, title }: { path: string; title: string }) {
  const [log, setLog] = useState<Log | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(false);

  async function open(event: React.SyntheticEvent<HTMLDetailsElement>) {
    if (!event.currentTarget.open || log || loading) return;
    setLoading(true);
    setFailed(false);
    try {
      setLog(await api<Log>(path));
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <details className="download-log" onToggle={(e) => void open(e)}>
      <summary aria-label={`Details of the download of ${title}`}>
        Details
      </summary>
      {loading && <p className="muted">Loading the details…</p>}
      {failed && (
        <p className="inline-error">Couldn’t load the details. Try again.</p>
      )}
      {log && log.lines.length === 0 && (
        <p className="muted">Nothing has happened yet.</p>
      )}
      {log && log.lines.length > 0 && (
        <ol>
          {log.lines.map((line, index) => (
            <li
              key={index}
              className={line.ok ? undefined : "download-log-failed"}
            >
              {line.ok ? (
                <Check size={14} aria-hidden="true" />
              ) : (
                <X size={14} aria-hidden="true" />
              )}
              <span>
                <span className="sr-only">
                  {line.ok ? "Done: " : "Problem: "}
                </span>
                {line.text}
              </span>
              {line.seconds != null && line.seconds >= 0.1 && (
                <small className="pig-amount">
                  {line.seconds.toFixed(1)} s
                </small>
              )}
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}
