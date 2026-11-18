import { notify, notifyError } from "./notices";
import { SESSION_UNKNOWN, expectedSession, withSession } from "./sessionId";

/**
 * Undo for an action the server cannot take back (D-79, #183): it shows as
 * done at once and is sent after `UNDO_DELAY_MS` unless Undo is pressed. A
 * page that is closed or hidden first sends it at once (keepalive), so a
 * pending action is only ever made sooner, never lost. Signing out sends it
 * at once too, before the session ends; a session lost any other way
 * (revoked, expired, another account in another tab) cancels it, so nothing
 * deferred ever runs after the session it was made in.
 */
export const UNDO_DELAY_MS = 10_000;

/** Sends the action; `keepalive` is true when the page is going away. */
type Send = (keepalive: boolean) => Promise<unknown>;

type Entry = {
  send: Send;
  timer: ReturnType<typeof setTimeout>;
  start: () => void;
  resolve: (sent: boolean) => void;
  reject: (error: unknown) => void;
  /** The session it was scheduled under; its write names that one. */
  session: string;
};

const pending = new Map<string, Entry>();
/** Set while signing out (drainPending until reopenDeferred). */
let signingOut = false;

/** Takes new actions again once a sign-out has finished. */
export function reopenDeferred() {
  signingOut = false;
}

/** Sends under way (from the timer, page hide or a flush) until settled. */
const inFlight = new Set<Promise<unknown>>();

function fire(key: string, keepalive: boolean): Promise<unknown> {
  const entry = pending.get(key);
  if (!entry) return Promise.resolve();
  pending.delete(key);
  clearTimeout(entry.timer);
  // From here it can no longer be undone.
  entry.start();
  const sending = (() => {
    try {
      return withSession(entry.session, () => entry.send(keepalive));
    } catch (error) {
      return Promise.reject(error);
    }
  })();
  const settled = sending.then(
    () => entry.resolve(true),
    (error: unknown) => entry.reject(error),
  );
  inFlight.add(settled);
  void settled.finally(() => inFlight.delete(settled));
  return settled;
}

/**
 * Schedules `send` under `key`. `started` resolves the moment sending
 * begins, after which it can no longer be undone; `done` resolves true once
 * it was sent, false if it was undone, and rejects with the send's error.
 * Scheduling a key that is already pending sends the earlier one at once.
 */
export function defer(
  key: string,
  send: Send,
  delay = UNDO_DELAY_MS,
): {
  undo: () => boolean;
  done: Promise<boolean>;
  started: Promise<void>;
} | null {
  // Refused (null) before anything shows as done: while signing out only
  // what was already accepted is sent, and with no session known nothing
  // can say whose it is (#250). The caller changes nothing on screen.
  if (signingOut) {
    notify("Signing out…", "signing-out");
    return null;
  }
  // Bound to the session it was started under.
  const session = expectedSession();
  if (!session) {
    notifyError(SESSION_UNKNOWN);
    return null;
  }
  fire(key, false);
  let resolve!: (sent: boolean) => void;
  let reject!: (error: unknown) => void;
  let start!: () => void;
  const done = new Promise<boolean>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  const started = new Promise<void>((yes) => (start = yes));
  const timer = setTimeout(() => fire(key, false), delay);
  const entry = { send, timer, start, resolve, reject, session };
  pending.set(key, entry);
  return {
    /** True when it was still pending and is now cancelled. */
    undo() {
      if (pending.get(key) !== entry) return false;
      pending.delete(key);
      clearTimeout(timer);
      resolve(false);
      return true;
    },
    done,
    started,
  };
}

/** Whether `key` is waiting to be sent (its effect is shown already). */
export function isPending(key: string) {
  return pending.has(key);
}

/** Sends everything pending at once (keepalive), as the page goes away or
 * before signing out; resolves once every send has finished. */
export function flushPending(): Promise<void> {
  return Promise.allSettled(
    [...pending.keys()].map((key) => fire(key, true)),
  ).then(() => {});
}

/**
 * Before signing out: sends everything still waiting (keepalive) and
 * resolves once every send under way has settled, those already started by
 * the timer or a page hide included, so the logout never overtakes one.
 */
export function drainPending(): Promise<void> {
  // From here new actions are refused; only accepted ones are sent.
  signingOut = true;
  for (const key of [...pending.keys()]) void fire(key, true);
  return Promise.allSettled([...inFlight]).then(() => {});
}

/** Cancels everything pending: the session it was made in has ended. Each
 * one settles as not sent (`done` resolves false). */
export function cancelPending() {
  for (const [key, entry] of [...pending]) {
    pending.delete(key);
    clearTimeout(entry.timer);
    entry.resolve(false);
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => void flushPending());
  // Sign-out has sent what was pending already; any other end cancels it.
  window.addEventListener("music-session-cleared", cancelPending);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") void flushPending();
  });
}
