import { useEffect, useRef, useState } from "react";

/** How long an Undo stays on screen (D-79, #183). */
export const UNDO_SHOWN_MS = 15_000;

type Offer = {
  id: number;
  message: string;
  undo: () => Promise<unknown> | unknown;
  /** Said once Undo has worked. */
  undone: string;
  /** A deferred action's start: its Undo leaves as sending begins. */
  sent?: Promise<unknown>;
};
type Note = { id: number; text: string };

const EVENT = "pig-undo-offer";
let next = 0;

/**
 * Destructive actions act at once and offer Undo here (D-79). The bar lives
 * in the app shell, so it stays when the action leaves the page (a deleted
 * playlist or a removed album). Each action keeps its own Undo until its
 * time is up; one that waits to be sent (`sent`) offers Undo only until it
 * is sent.
 */
export function offerUndo(
  message: string,
  undo: Offer["undo"],
  undone = "Undone",
  sent?: Promise<unknown>,
) {
  window.dispatchEvent(
    new CustomEvent<Offer>(EVENT, {
      detail: { id: ++next, message, undo, undone, sent },
    }),
  );
}

export function UndoBar() {
  const [offers, setOffers] = useState<Offer[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  useEffect(() => {
    const live = timers.current;
    const later = (run: () => void, ms: number) => {
      const timer = setTimeout(() => {
        live.delete(timer);
        run();
      }, ms);
      live.add(timer);
    };
    const offered = (event: Event) => {
      const offer = (event as CustomEvent<Offer>).detail;
      const drop = () =>
        setOffers((all) => all.filter((o) => o.id !== offer.id));
      setOffers((all) => [...all, offer]);
      later(drop, UNDO_SHOWN_MS);
      // Being sent: it can no longer be undone.
      offer.sent?.then(drop, drop);
    };
    window.addEventListener(EVENT, offered);
    return () => {
      window.removeEventListener(EVENT, offered);
      for (const timer of live) clearTimeout(timer);
      live.clear();
    };
  }, []);
  function say(text: string) {
    const note = { id: ++next, text };
    setNotes((all) => [...all, note]);
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      setNotes((all) => all.filter((n) => n.id !== note.id));
    }, UNDO_SHOWN_MS);
    timers.current.add(timer);
  }
  if (!offers.length && !notes.length) return null;
  return (
    <div className="personal-notice undo-bar" role="status">
      {offers.map((offer) => (
        <p key={offer.id}>
          {offer.message}{" "}
          <button
            type="button"
            disabled={busy !== null}
            onClick={async () => {
              setBusy(offer.id);
              try {
                await offer.undo();
                say(offer.undone);
              } catch (e) {
                say(`Undo didn't work: ${(e as Error).message}`);
              } finally {
                // Only this offer goes; newer ones stay.
                setOffers((all) => all.filter((o) => o.id !== offer.id));
                setBusy(null);
              }
            }}
          >
            Undo
          </button>
        </p>
      ))}
      {notes.map((note) => (
        <p key={note.id}>{note.text}</p>
      ))}
    </div>
  );
}
