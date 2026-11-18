import { notifyError } from "./notices";
import { SESSION_UNKNOWN, expectedSession } from "./sessionId";

/**
 * The in-app confirmation for what cannot be undone (D-79, #250): emptying
 * Trash, signing out everywhere else, resetting or disabling a person. The
 * app shell's `ConfirmDialog` shows one question at a time; anything that
 * can be undone acts at once with Undo instead (see notices.ts).
 */
export type ConfirmQuestion = {
  /** Names the thing and the action, e.g. "Disable Sam's account?" */
  title: string;
  /** Plain words: what happens, and that it can't be undone. */
  body: string;
  /** The action button's words, e.g. "Delete for good". */
  action: string;
  /** Red action button: it removes or locks something for good. */
  destructive?: boolean;
};

export type AskedQuestion = ConfirmQuestion & { id: number };

let asked:
  | (AskedQuestion & {
      answer: (session: string | null) => void;
      generation: number;
      session: string;
    })
  | null = null;
let next = 0;
/** Counts session ends: a question belongs to the session that asked it. */
let generation = 0;
const listeners = new Set<(question: AskedQuestion | null) => void>();

function publish() {
  for (const listener of listeners) listener(asked);
}

/**
 * The session ended (signed out, revoked or expired) or the dialog's host
 * left: the open question is cancelled, and nothing asked for that session
 * can be shown or approved afterwards.
 */
export function endConfirmSession() {
  generation++;
  if (asked) answerConfirm(asked.id, false);
}

/**
 * Asks `question` in the app's dialog. Resolves with the session it was
 * asked under when the action is chosen (the write names it, so the server
 * refuses it if another sign-in holds the cookie by then), or null for
 * Cancel or Escape. `forSession` is the session the caller started under
 * (default: now); with none known, or another one by now, nothing is asked.
 * A new question cancels one still open.
 */
export function askToConfirm(
  question: ConfirmQuestion,
  forSession = expectedSession(),
): Promise<string | null> {
  if (!forSession) {
    notifyError(SESSION_UNKNOWN);
    return Promise.resolve(null);
  }
  if (forSession !== expectedSession()) return Promise.resolve(null);
  if (asked) answerConfirm(asked.id, false);
  return new Promise<string | null>((resolve) => {
    asked = {
      ...question,
      id: ++next,
      answer: resolve,
      generation,
      session: forSession,
    };
    publish();
  });
}

/**
 * Answers question `id`. A handler left from an earlier question (its id no
 * longer open) is ignored, so it can never approve a newer one.
 */
export function answerConfirm(id: number, yes: boolean) {
  const question = asked;
  if (!question || question.id !== id) return;
  asked = null;
  publish();
  question.answer(
    yes &&
      question.generation === generation &&
      question.session === expectedSession()
      ? question.session
      : null,
  );
}

/** Calls `listener` with the open question (or null) on every change. */
export function subscribeConfirm(
  listener: (question: AskedQuestion | null) => void,
) {
  listeners.add(listener);
  listener(asked);
  return () => {
    listeners.delete(listener);
  };
}

if (typeof window !== "undefined")
  window.addEventListener("music-session-cleared", endConfirmSession);
