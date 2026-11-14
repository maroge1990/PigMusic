import { api } from "./api";
import { restoreFromTrash } from "./trashUndo";
import { offerUndo } from "./UndoBar";
import { useState } from "react";

/** Owner-only: move the server's files to recoverable trash for everyone. */
export function RemoveFromServer({
  kind,
  id,
  title,
  removed,
}: {
  kind: "tracks" | "albums" | "artists";
  id: string;
  title: string;
  removed?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function remove() {
    // Acts at once for everyone; Undo puts it back (D-79).
    setBusy(true);
    setError("");
    try {
      // api() turns any error answer into a sentence, never "[object Object]".
      const done = await api<{ restorable?: string[] }>(
        `/v1/admin/${kind}/${id}/remove`,
        { method: "POST" },
      );
      window.dispatchEvent(new Event("music-library-changed"));
      if (done.restorable?.length)
        offerUndo(
          `${title} removed from the server for everyone.${kind === "artists" ? " Artist monitoring is off." : ""}`,
          () => restoreFromTrash(done.restorable!),
          `${title} is back.${kind === "artists" ? " Turn artist monitoring on again from the artist page." : ""}`,
        );
      removed?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button
        className="danger-text-button"
        disabled={busy}
        onClick={() => void remove()}
      >
        Remove from server
      </button>
      {error && (
        <span className="inline-error" role="alert">
          {error}
        </span>
      )}
    </>
  );
}
