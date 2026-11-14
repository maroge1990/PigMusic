import { useEffect, useRef, useState } from "react";

/** How long an Undo stays on screen (D-79, #183). */
export const UNDO_SHOWN_MS = 15_000;

type Offer = {
  message: string;
  undo: () => Promise<unknown> | unknown;
  /** Said once Undo has worked. */
  undone: string;
};

const EVENT = "pig-undo-offer";

/**
 * Destructive actions act at once and offer Undo here (D-79). The bar lives
 * in the app shell, so it stays when the action leaves the page (a deleted
 * playlist or a removed album).
 */
export function offerUndo(
  message: string,
  undo: Offer["undo"],
  undone = "Undone",
) {
  window.dispatchEvent(
    new CustomEvent<Offer>(EVENT, { detail: { message, undo, undone } }),
  );
}

export function UndoBar() {
  const [offer, setOffer] = useState<Offer | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    const offered = (event: Event) => {
      setOffer((event as CustomEvent<Offer>).detail);
      setNote("");
    };
    window.addEventListener(EVENT, offered);
    return () => window.removeEventListener(EVENT, offered);
  }, []);
  useEffect(() => {
    clearTimeout(timer.current);
    if (offer || note)
      timer.current = setTimeout(() => {
        setOffer(null);
        setNote("");
      }, UNDO_SHOWN_MS);
    return () => clearTimeout(timer.current);
  }, [offer, note]);
  if (!offer && !note) return null;
  return (
    <div className="personal-notice undo-bar" role="status">
      <p>{offer ? offer.message : note}</p>
      {offer && (
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            const current = offer;
            setBusy(true);
            try {
              await current.undo();
              setOffer(null);
              setNote(current.undone);
            } catch (e) {
              setOffer(null);
              setNote(`Undo didn't work: ${(e as Error).message}`);
            } finally {
              setBusy(false);
            }
          }}
        >
          Undo
        </button>
      )}
    </div>
  );
}
