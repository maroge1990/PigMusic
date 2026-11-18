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
      answer: (yes: boolean) => void;
      session: number;
      identity: string | null;
    })
  | null = null;
let next = 0;
/** Counts sign-ins: a question belongs to the session that asked it. */
let session = 0;
const listeners = new Set<(question: AskedQuestion | null) => void>();
/** The server's id for this tab's session, and how to read it again. */
let identity: string | null = null;
let serverSession: (() => Promise<string | null>) | null = null;
let checkFailed = () => {};

/**
 * Says which server session this tab is signed in with, and how to ask the
 * server for the one it has now. Another tab can replace the shared cookie,
 * so a confirmed action rechecks it before it goes ahead.
 */
export function setConfirmIdentity(
  id: string | null,
  read: (() => Promise<string | null>) | null,
  failed: () => void = () => {},
) {
  identity = id;
  serverSession = read;
  checkFailed = failed;
}

function publish() {
  for (const listener of listeners) listener(asked);
}

/** The session a caller started under; pass it to askToConfirm after an
 * await, so a question for an account that has gone is never shown. */
export function confirmSession() {
  return session;
}

/**
 * The session ended (signed out, revoked or expired) or the dialog's host
 * left: the open question is cancelled, and nothing asked for that session
 * can be shown or approved afterwards.
 */
export function endConfirmSession() {
  session++;
  if (asked) answerConfirm(asked.id, false);
}

/**
 * Asks `question` in the app's dialog; resolves true for the action, false
 * for Cancel or Escape. A new question cancels one still open; a question
 * for an ended `forSession` resolves false without being shown.
 */
export function askToConfirm(
  question: ConfirmQuestion,
  forSession = session,
): Promise<boolean> {
  if (forSession !== session) return Promise.resolve(false);
  if (asked) answerConfirm(asked.id, false);
  return new Promise<boolean>((resolve) => {
    asked = { ...question, id: ++next, answer: resolve, session, identity };
    publish();
  });
}

/**
 * Answers question `id`. A handler left from an earlier question (its id no
 * longer open) is ignored, so it can never approve a newer one. Yes goes
 * ahead only once the server still has the session that asked; if it
 * can't be checked, nothing is done.
 */
export function answerConfirm(id: number, yes: boolean) {
  const question = asked;
  if (!question || question.id !== id) return;
  asked = null;
  publish();
  if (!yes) return question.answer(false);
  const same = () => question.session === session;
  const read = serverSession;
  if (!read) return question.answer(same());
  read().then(
    (now) => question.answer(same() && now === question.identity),
    () => {
      question.answer(false);
      checkFailed();
    },
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
