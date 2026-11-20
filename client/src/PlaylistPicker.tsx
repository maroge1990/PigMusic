import { useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Ref } from "react";
import { ListPlus, Plus, X } from "lucide-react";
import {
  AttemptEnded,
  ReportedFailure,
  createAttempt,
  forAttempt,
  loadPlaylists,
  newAttempt,
} from "./playlists";
import { EmptyLine, InlineError } from "./EmptyState";
import { focusMenuItem, menuKeys } from "./menuKeys";
import { isUnconfirmed } from "./api";
import { notify, notifyError } from "./notices";

type Playlist = { id: string; name: string };

export type PlaylistPickerHandle = {
  /** Opens the picker next to `anchor`; focus returns there on close. */
  open: (anchor: HTMLElement) => void;
};

/**
 * The one "Add to playlist" surface (#249): from a song or collection menu
 * and from Now Playing. A list of playlists plus New playlist; arrow keys,
 * Home and End move, Escape closes and focus goes back to what opened it.
 */
export function PlaylistPicker({
  title,
  add,
  button = false,
  ref,
}: {
  title: string;
  /** Show its own round button (Now Playing); menus open it by `ref`. */
  button?: boolean;
  add: (id: string) => Promise<unknown>;
  ref?: Ref<PlaylistPickerHandle>;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const anchor = useRef<HTMLElement | null>(null);
  const popup = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [items, setItems] = useState<Playlist[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  // The playlist this dialog already made: a failed add retries into it,
  // never a second create (#275). Both belong to one opening of the
  // dialog: a create that answers after it closed changes nothing in the
  // next one.
  const made = useRef<string | null>(null);
  const opening = useRef(0);
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
      focusMenuItem(popup.current, 0);
  }, [items]);
  async function select(id: string) {
    setBusy(true);
    setError("");
    try {
      await add(id);
      popup.current?.hidePopover();
      notify("Added to playlist");
    } catch (e) {
      // Already said in the notice area, with its Try again.
      if (e instanceof ReportedFailure) popup.current?.hidePopover();
      else setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function open(from: HTMLElement) {
    anchor.current = from;
    const rect = from.getBoundingClientRect();
    const width = Math.min(280, window.innerWidth - 16);
    setPosition({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      ...(rect.top > window.innerHeight / 2
        ? { bottom: window.innerHeight - rect.top + 8 }
        : { top: rect.bottom + 8 }),
    });
    setError("");
    popup.current?.showPopover();
    focusMenuItem(popup.current, 0);
    try {
      setItems(await load());
    } catch {
      setError("Couldn't load your playlists. Try again.");
    }
  }
  const load = loadPlaylists;
  useImperativeHandle(ref, () => ({ open: (from) => void open(from) }));
  const returnFocus = () => {
    if (anchor.current?.isConnected) anchor.current.focus();
  };
  return (
    <span className="playlist-picker">
      {button && (
        <button
          ref={trigger}
          className="round-action"
          aria-label={`Add ${title} to playlist`}
          aria-haspopup="menu"
          onClick={() => void open(trigger.current!)}
        >
          <ListPlus size={18} />
        </button>
      )}
      <div
        ref={popup}
        popover="auto"
        className="playlist-popover song-menu pig-glass"
        style={position}
        role="menu"
        aria-label={`Add ${title} to playlist`}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            popup.current?.hidePopover();
            returnFocus();
            return;
          }
          menuKeys(e);
        }}
      >
        <div className="song-menu-group">
          {items.map((p) => (
            <button
              key={p.id}
              type="button"
              role="menuitem"
              disabled={busy}
              onClick={() => void select(p.id)}
            >
              <span>{p.name}</span>
              <ListPlus size={18} />
            </button>
          ))}
          {!items.length && <EmptyLine>No playlists yet</EmptyLine>}
        </div>
        <div className="song-menu-group">
          <button
            type="button"
            role="menuitem"
            className="new-playlist-option"
            onClick={() => {
              popup.current?.hidePopover();
              setCreating(true);
            }}
          >
            <span>New playlist</span>
            <Plus size={18} />
          </button>
        </div>
        {error && <InlineError>{error}</InlineError>}
      </div>
      {creating && (
        <dialog
          ref={dialog}
          className="new-playlist-dialog"
          onClose={() => {
            setCreating(false);
            made.current = null;
            opening.current++;
            setBusy(false);
            returnFocus();
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
              const mine = opening.current;
              const open = () => opening.current === mine;
              // This submission's own key, and the session it belongs to:
              // only its Try again reuses them (#288), and whatever comes of
              // it after that session ended is dropped unseen (Codex).
              const attempt = newAttempt(name);
              /** The notice's Try again: this same attempt (name, key and
               * songs), sent again, then the songs added to what it made. */
              const unconfirmed = (e: Error): number =>
                notifyError(
                  e.message,
                  (): Promise<unknown> =>
                    createAttempt(attempt)
                      .then((p) => forAttempt(attempt, () => add(p.id)))
                      .then(() => notify("Playlist created and songs added"))
                      .catch((again): void => {
                        if (again instanceof AttemptEnded) return;
                        if (again instanceof ReportedFailure) return;
                        if (isUnconfirmed(again)) {
                          unconfirmed(again);
                          return;
                        }
                        notifyError(again.message, undefined, "new-playlist");
                      }),
                  "new-playlist",
                );
              const playlist = made.current
                ? Promise.resolve(made.current)
                : createAttempt(attempt).then((p) => {
                    if (open()) made.current = p.id;
                    return p.id;
                  });
              let ended = false;
              void playlist
                .then((id) => forAttempt(attempt, () => add(id)))
                .then(() => {
                  if (open()) dialog.current?.close();
                  notify("Playlist created and songs added");
                })
                .catch((e) => {
                  // The session it was made in ended: nothing is shown.
                  if (e instanceof AttemptEnded) {
                    ended = true;
                    return;
                  }
                  // Already said in the notice area, with its Try again
                  // into the playlist just made.
                  if (e instanceof ReportedFailure) {
                    if (open()) dialog.current?.close();
                    return;
                  }
                  if (!isUnconfirmed(e)) {
                    if (open()) setError(e.message);
                    return;
                  }
                  // The playlist may have been made (#275): no second
                  // Create from here; the notice says to check first, and
                  // its Try again sends this attempt again (#288).
                  if (open()) dialog.current?.close();
                  unconfirmed(e);
                })
                .finally(() => !ended && open() && setBusy(false));
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
    </span>
  );
}
