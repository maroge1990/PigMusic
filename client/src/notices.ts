/**
 * The one place the web client says how an action went (#250, D-79). Every
 * page, member and admin alike, adds notices here; the app shell's
 * `NoticeArea` shows them, newest last, at most `MAX_SHOWN` at a time.
 *
 * - info: a plain success or update; clears itself after `NOTICE_SHOWN_MS`.
 * - action: a success that offers Undo; stays for the Undo time
 *   (`UNDO_SHOWN_MS`) or until a deferred action starts sending.
 * - error: stays until it is closed or another error replaces it; it may
 *   offer Try again.
 * Every notice can be closed.
 */
export const NOTICE_SHOWN_MS = 5_000;
/** How long an Undo stays on screen (D-79, #183). */
export const UNDO_SHOWN_MS = 15_000;
export const MAX_SHOWN = 3;

export type NoticeKind = "info" | "action" | "error";

export type NoticeAction = {
  label: "Undo" | "Try again";
  run: () => Promise<unknown> | unknown;
  /** Said once Undo has worked. */
  done?: string;
};

export type Notice = {
  id: number;
  kind: NoticeKind;
  text: string;
  action?: NoticeAction;
  /** A notice with the same key replaces the earlier one. */
  key?: string;
};

/** Success and updates are polite; failures interrupt. */
export const roleOf = (kind: NoticeKind) =>
  kind === "error" ? "alert" : "status";

let notices: Notice[] = [];
let next = 0;
const timers = new Map<number, ReturnType<typeof setTimeout>>();
const listeners = new Set<(all: Notice[]) => void>();

function publish() {
  for (const listener of listeners) listener(notices);
}

function remove(id: number) {
  clearTimeout(timers.get(id));
  timers.delete(id);
  const left = notices.filter((n) => n.id !== id);
  if (left.length === notices.length) return;
  notices = left;
  publish();
}

function add(notice: Omit<Notice, "id">, shownFor?: number): number {
  const id = ++next;
  for (const old of notices)
    if (
      (notice.key && old.key === notice.key) ||
      (notice.kind === "error" && old.kind === "error")
    )
      remove(old.id);
  notices = [...notices, { ...notice, id }];
  if (shownFor !== undefined)
    timers.set(
      id,
      setTimeout(() => remove(id), shownFor),
    );
  publish();
  return id;
}

/** Says something went well, or what changed; it clears by itself. */
export function notify(text: string, key?: string) {
  return add({ kind: "info", text, key }, NOTICE_SHOWN_MS);
}

/** Says something failed; it stays until closed or replaced. */
export function notifyError(
  text: string,
  tryAgain?: () => Promise<unknown> | unknown,
) {
  return add({
    kind: "error",
    text,
    action: tryAgain && { label: "Try again", run: tryAgain },
  });
}

/**
 * Destructive actions act at once and offer Undo here (D-79). The notice
 * area lives in the app shell, so it stays when the action leaves the page
 * (a deleted playlist or a removed album). Each action keeps its own Undo
 * until its time is up; one that waits to be sent (`sent`) offers Undo only
 * until it is sent.
 */
export function offerUndo(
  message: string,
  undo: () => Promise<unknown> | unknown,
  undone = "Undone",
  sent?: Promise<unknown>,
  /** A shorter Undo time, when the server keeps it for less. */
  shownFor = UNDO_SHOWN_MS,
) {
  const id = add(
    {
      kind: "action",
      text: message,
      action: { label: "Undo", run: undo, done: undone },
    },
    Math.max(0, Math.min(shownFor, UNDO_SHOWN_MS)),
  );
  // Being sent: it can no longer be undone.
  sent?.then(
    () => remove(id),
    () => remove(id),
  );
  return id;
}

/** Closes a notice. */
export function dismiss(id: number) {
  remove(id);
}

/**
 * Runs a notice's action. Undo says what came back, or why it couldn't;
 * Try again closes the error and leaves the outcome to the retried action.
 */
export async function runAction(id: number) {
  const notice = notices.find((n) => n.id === id);
  if (!notice?.action) return;
  if (notice.action.label === "Try again") {
    remove(id);
    await notice.action.run();
    return;
  }
  try {
    await notice.action.run();
    remove(id);
    notify(notice.action.done || "Undone");
  } catch (e) {
    remove(id);
    notifyError(`Couldn't undo. ${(e as Error).message}`);
  }
}

/** The notices on screen: the newest `MAX_SHOWN`, oldest first. */
export function shown(all = notices) {
  return all.slice(-MAX_SHOWN);
}

/** Calls `listener` with every change; returns the unsubscribe. */
export function subscribe(listener: (all: Notice[]) => void) {
  listeners.add(listener);
  listener(notices);
  return () => {
    listeners.delete(listener);
  };
}

/** Clears every notice (tests, and signing out). */
export function clearNotices() {
  for (const timer of timers.values()) clearTimeout(timer);
  timers.clear();
  notices = [];
  publish();
}

// A notice, and above all its Undo, belongs to the account that acted: it
// leaves when that session ends, as the old Undo bar left with the shell.
if (typeof window !== "undefined")
  window.addEventListener("music-session-cleared", clearNotices);
