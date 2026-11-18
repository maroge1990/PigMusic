import { notify, notifyError } from "./notices";
import { askToConfirm } from "./confirmDialog";
import { SESSION_UNKNOWN, expectedSession, sessionHeader } from "./sessionId";
import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { api } from "./api";
import { formatCount } from "./format";
import { go } from "./routes";

type Preview = {
  playlists: number;
  tracks: number;
  saved: number;
  household: number;
  missing: number;
};

const xml = { "Content-Type": "application/xml" };

function count(n: number, word: string) {
  return `${formatCount(n)} ${word}${n === 1 ? "" : "s"}`;
}

/** The one place to start an Apple Music XML import: preview first, then
 * save the playlists (never downloads). The owner's whole-library review
 * starts from the same file, at the bottom. */
export function AppleImport({
  owner,
  onImported,
}: {
  owner: boolean;
  onImported: () => Promise<void> | void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setFile(null);
    setPreview(null);
    if (input.current) input.current.value = "";
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      notifyError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const choose = (chosen?: File) =>
    chosen &&
    run(async () => {
      setFile(chosen);
      setPreview(null);
      setPreview(
        await api<Preview>("/v1/imports/apple/preview", {
          method: "POST",
          headers: xml,
          body: chosen,
        }),
      );
    });

  const importPlaylists = () =>
    run(async () => {
      if (!file) return;
      const result = await api<{ playlists: number }>("/v1/imports/apple", {
        method: "POST",
        headers: xml,
        body: file,
      });
      reset();
      notify(`Imported ${count(result.playlists, "playlist")}`);
      await onImported();
    });

  const reviewWholeLibrary = () =>
    run(async () => {
      if (!file) return;
      // Bound to the session it was started under: the replacing write
      // names it, so it is refused if another sign-in holds the cookie.
      const started = expectedSession();
      if (!started) throw new Error(SESSION_UNKNOWN);
      const current = await api<{ stats?: { selected?: number } }>(
        "/v1/admin/migration",
      );
      const session = current.stats?.selected
        ? await askToConfirm(
            {
              title: "Replace the whole-library review?",
              body: "The new export replaces the current review and clears the songs selected in it. This can't be undone.",
              action: "Replace review",
              destructive: true,
            },
            started,
          )
        : started;
      // Not if the session ended during the read (the server would refuse
      // it under another sign-in anyway).
      if (!session || session !== expectedSession()) return;
      await api("/v1/admin/migration/import", {
        method: "POST",
        headers: { ...xml, ...sessionHeader(session) },
        body: file,
      });
      reset();
      go("/admin/migration?imported=1");
    });

  return (
    <section className="account-section apple-import">
      <h2>Import from Apple Music</h2>
      <p>
        Choose an Apple Music XML export (File › Library › Export Library). You
        see what it contains before anything is saved, and importing never
        downloads music.
      </p>
      <input
        ref={input}
        type="file"
        className="sr-only"
        accept=".xml,application/xml,text/xml"
        aria-label="Apple Music XML export"
        onChange={(e) => void choose(e.target.files?.[0])}
      />
      {!preview && (
        <button
          className="secondary-button"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          <Upload size={16} aria-hidden="true" />
          {busy ? "Reading…" : "Choose XML export"}
        </button>
      )}
      {preview && file && (
        <>
          <p>
            <strong>{file.name}</strong>: {count(preview.playlists, "playlist")}
            , {count(preview.tracks, "song")}. {formatCount(preview.saved)}{" "}
            already in your library, {formatCount(preview.household)} on the
            server, {formatCount(preview.missing)} missing. Missing songs stay
            in the playlists without downloading.
          </p>
          <div className="release-actions">
            <button
              className="primary-button"
              disabled={busy || !preview.playlists}
              onClick={() => void importPlaylists()}
            >
              Import {count(preview.playlists, "playlist")}
            </button>
            <button
              className="secondary-button"
              disabled={busy}
              onClick={reset}
            >
              Cancel
            </button>
          </div>
          {owner && (
            <details className="owner-tools">
              <summary>Admin: review the whole library</summary>
              <div className="owner-tools-body">
                <p>
                  Opens Admin › Library migration with this export, where you
                  choose which songs to add to the server. Nothing downloads
                  until you submit there.
                </p>
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => void reviewWholeLibrary()}
                >
                  Review whole library
                </button>
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}
