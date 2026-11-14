import { useEffect, useRef, useState } from "react";
import { Check, X } from "lucide-react";
import { api } from "./api";

type Line = { text: string; ok: boolean; seconds?: number | null };
type Log = { id: string; status: string; lines: Line[] };

/** One download told step by step (#197): what was searched, the copies
 * found, each one tried and why it was refused, the checks, any conversion
 * and where the file went. Asked for each time it opens, and again while
 * open whenever the request changes (`version`: its status and update
 * time), so a retry or a finished download never shows an old answer. */
export function DownloadLog({
  path,
  title,
  version,
}: {
  path: string;
  title: string;
  version?: string;
}) {
  const [open, setOpen] = useState(false);
  const [log, setLog] = useState<Log | null>(null);
  const [failed, setFailed] = useState(false);
  const serial = useRef(0);

  useEffect(() => {
    if (!open) return;
    const asked = ++serial.current;
    setFailed(false);
    api<Log>(path).then(
      (answer) => {
        if (asked === serial.current) setLog(answer);
      },
      () => {
        if (asked === serial.current) setFailed(true);
      },
    );
  }, [open, path, version]);

  return (
    <details
      className="download-log"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary aria-label={`Details of the download of ${title}`}>
        Details
      </summary>
      {open && !log && !failed && <p className="muted">Loading the details…</p>}
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
