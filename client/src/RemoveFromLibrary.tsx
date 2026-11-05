import { useEffect, useRef } from "react";

/** What removing songs from your library would do, from the server. */
export type RemovalPreview = {
  confirmation: string;
  delete_count: number;
  keep_count: number;
  items: { track_id: string; delete_file: boolean; kept_for: string[] }[];
};
export type Removal = { preview: RemovalPreview; ids: string[] };

/** Asks before removing songs, saying what happens to their files. */
export function RemovalDialog({
  removal,
  cancel,
  confirm,
}: {
  removal: Removal;
  cancel: () => void;
  confirm: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, [removal]);
  return (
    <dialog ref={dialog} onCancel={cancel}>
      <h2>Remove from your library?</h2>
      <p>
        {removal.preview.delete_count
          ? `${removal.preview.delete_count} music files will move to recoverable trash on the server.`
          : "The music files will stay on the server."}
      </p>
      {removal.preview.items
        .filter((i) => !i.delete_file)
        .map((i) => (
          <p key={i.track_id}>
            Kept for {i.kept_for.join(", ") || "another pending request"}.
          </p>
        ))}
      <button onClick={cancel}>Cancel</button>
      <button onClick={confirm}>Remove</button>
    </dialog>
  );
}

/** Shown after a removal until its undo window closes. */
export function UndoRemoval({ undo }: { undo: () => void }) {
  return (
    <p className="personal-status" role="status">
      Removed from your library. <button onClick={undo}>Undo</button>
    </p>
  );
}
