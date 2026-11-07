import { useEffect, useRef, useState } from "react";
import { ListPlus, Plus, X } from "lucide-react";
import { EmptyLine, InlineError } from "./EmptyState";

type Playlist = { id: string; name: string };
export function PlaylistPicker({
  title,
  load,
  create,
  add,
  menuItem = false,
}: {
  title: string;
  /** Render the trigger as a row in a song's ⋯ menu. */
  menuItem?: boolean;
  load: () => Promise<Playlist[]>;
  create: (name: string) => Promise<Playlist>;
  add: (id: string) => Promise<unknown>;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [items, setItems] = useState<Playlist[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [creating, setCreating] = useState(false);
  const [position, setPosition] = useState<{
    left: number;
    top?: number;
    bottom?: number;
  }>({ left: 8, top: 8 });
  useEffect(() => {
    if (creating) {
      dialog.current?.showModal();
      dialog.current
        ?.querySelector<HTMLInputElement>('input[name="name"]')
        ?.focus();
    }
  }, [creating]);
  useEffect(() => {
    if (popup.current?.matches(":popover-open"))
      popup.current.querySelector<HTMLButtonElement>("button")?.focus();
  }, [items]);
  async function select(id: string) {
    setBusy(true);
    setError("");
    try {
      await add(id);
      popup.current?.hidePopover();
      setNotice("Added to playlist");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function open() {
    const rect = trigger.current!.getBoundingClientRect();
    const width = Math.min(280, window.innerWidth - 16);
    setPosition({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      ...(rect.top > window.innerHeight / 2
        ? { bottom: window.innerHeight - rect.top + 8 }
        : { top: rect.bottom + 8 }),
    });
    setError("");
    setNotice("");
    popup.current?.showPopover();
    try {
      setItems(await load());
    } catch {
      setError("Playlists unavailable. Try again.");
    }
  }
  return (
    <span className="playlist-picker">
      <button
        ref={trigger}
        className={menuItem ? undefined : "round-action"}
        role={menuItem ? "menuitem" : undefined}
        aria-label={menuItem ? undefined : `Add ${title} to playlist`}
        aria-haspopup="dialog"
        onClick={() => void open()}
      >
        {menuItem && <span>Add to playlist…</span>}
        <ListPlus size={18} />
      </button>
      <div
        ref={popup}
        popover="auto"
        className="playlist-popover"
        style={position}
        role="dialog"
        aria-label={`Add ${title} to playlist`}
      >
        <strong>Add to playlist</strong>
        {items.map((p) => (
          <button key={p.id} disabled={busy} onClick={() => void select(p.id)}>
            {p.name}
          </button>
        ))}
        {!items.length && <EmptyLine>No playlists yet</EmptyLine>}
        <button
          className="new-playlist-option"
          onClick={() => {
            popup.current?.hidePopover();
            setCreating(true);
          }}
        >
          <Plus size={16} /> New playlist
        </button>
        {error && <InlineError>{error}</InlineError>}
      </div>
      {creating && (
        <dialog
          ref={dialog}
          className="new-playlist-dialog"
          onClose={() => {
            setCreating(false);
            trigger.current?.focus();
          }}
        >
          <div className="modal-heading">
            <h2>New playlist</h2>
            <button aria-label="Close" onClick={() => dialog.current?.close()}>
              <X size={18} />
            </button>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const name = String(
                new FormData(e.currentTarget).get("name") || "",
              ).trim();
              if (!name) return;
              setBusy(true);
              setError("");
              void create(name)
                .then((p) => add(p.id))
                .then(() => {
                  setNotice("Playlist created and song added");
                  dialog.current?.close();
                })
                .catch((e) => setError(e.message))
                .finally(() => setBusy(false));
            }}
          >
            <label>
              Playlist name
              <input name="name" autoFocus required maxLength={100} />
            </label>
            <p>{title} will be added to this playlist.</p>
            <button type="submit" className="primary-button" disabled={busy}>
              Create playlist
            </button>
          </form>
          {error && <InlineError>{error}</InlineError>}
        </dialog>
      )}
      <span className="sr-only" role="status">
        {notice}
      </span>
    </span>
  );
}
