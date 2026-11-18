import { useEffect, useRef, useState } from "react";
import {
  answerConfirm,
  subscribeConfirm,
  type AskedQuestion,
} from "./confirmDialog";

/**
 * The app's one confirmation dialog (#250): a modal `<dialog>` with Cancel,
 * which has focus, and the action, red when it is destructive. Escape
 * cancels.
 */
export function ConfirmDialog() {
  const [question, setQuestion] = useState<AskedQuestion | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => subscribeConfirm(setQuestion), []);
  useEffect(() => {
    if (!question) return;
    if (!dialog.current?.open) dialog.current?.showModal();
    cancel.current?.focus();
  }, [question?.id]);
  if (!question) return null;
  const finish = (yes: boolean) => {
    // Closing first hands focus back to what opened the dialog.
    dialog.current?.close();
    answerConfirm(yes);
  };
  return (
    <dialog
      ref={dialog}
      className="confirm-dialog"
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-body"
      onCancel={(e) => {
        e.preventDefault();
        finish(false);
      }}
    >
      <h2 id="confirm-dialog-title">{question.title}</h2>
      <p id="confirm-dialog-body">{question.body}</p>
      <div className="confirm-actions">
        <button
          ref={cancel}
          type="button"
          className="secondary-button"
          onClick={() => finish(false)}
        >
          Cancel
        </button>
        <button
          type="button"
          className={question.destructive ? "danger-button" : "primary-button"}
          onClick={() => finish(true)}
        >
          {question.action}
        </button>
      </div>
    </dialog>
  );
}
