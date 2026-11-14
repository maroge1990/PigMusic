/**
 * Undo for an action the server cannot take back (D-79, #183): it shows as
 * done at once and is sent after `UNDO_DELAY_MS` unless Undo is pressed. A
 * page that is closed or hidden first sends it at once (keepalive), so a
 * pending action is only ever made sooner, never lost.
 */
export const UNDO_DELAY_MS = 10_000;

/** Sends the action; `keepalive` is true when the page is going away. */
type Send = (keepalive: boolean) => Promise<unknown>;

type Entry = {
  send: Send;
  timer: ReturnType<typeof setTimeout>;
  resolve: (sent: boolean) => void;
  reject: (error: unknown) => void;
};

const pending = new Map<string, Entry>();

function fire(key: string, keepalive: boolean) {
  const entry = pending.get(key);
  if (!entry) return;
  pending.delete(key);
  clearTimeout(entry.timer);
  entry.send(keepalive).then(
    () => entry.resolve(true),
    (error: unknown) => entry.reject(error),
  );
}

/**
 * Schedules `send` under `key`. `done` resolves true once it was sent, false
 * if it was undone, and rejects with the send's error. Scheduling a key that
 * is already pending sends the earlier one at once first.
 */
export function defer(
  key: string,
  send: Send,
  delay = UNDO_DELAY_MS,
): { undo: () => boolean; done: Promise<boolean> } {
  fire(key, false);
  let resolve!: (sent: boolean) => void;
  let reject!: (error: unknown) => void;
  const done = new Promise<boolean>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  const timer = setTimeout(() => fire(key, false), delay);
  const entry = { send, timer, resolve, reject };
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
  };
}

/** Whether `key` is waiting to be sent (its effect is shown already). */
export function isPending(key: string) {
  return pending.has(key);
}

/** Sends everything pending at once, as the page goes away. */
export function flushPending() {
  for (const key of [...pending.keys()]) fire(key, true);
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", flushPending);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushPending();
  });
}
