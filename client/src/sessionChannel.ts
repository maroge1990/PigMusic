/**
 * Tells this browser's other PigMusic tabs that the sign-in changed. Tabs
 * share one sign-in cookie, so a sign-in or sign-out in one tab ends the
 * session every other tab was using: each of them then treats it as a lost
 * session (no question or deferred write survives it) and reads who is
 * signed in now. BroadcastChannel where the browser has it, else a
 * `storage` event.
 */
export type SessionChange = "signed-in" | "signed-out";

const NAME = "pig-music-session";

type Options = {
  BroadcastChannel?: typeof BroadcastChannel | null;
  target?: EventTarget;
  storage?: Pick<Storage, "setItem">;
};

export function openSessionChannel(options: Options = {}) {
  const Channel =
    options.BroadcastChannel === undefined
      ? globalThis.BroadcastChannel
      : options.BroadcastChannel;
  const target = options.target ?? globalThis.window;
  const storage =
    options.storage ??
    (() => {
      try {
        return globalThis.localStorage;
      } catch {
        return undefined;
      }
    })();
  const tab = Math.random().toString(36).slice(2);
  const channel = Channel ? new Channel(NAME) : null;
  let sent = 0;
  return {
    announce(change: SessionChange) {
      const message = { change, tab, at: ++sent };
      if (channel) return channel.postMessage(message);
      try {
        storage?.setItem(NAME, JSON.stringify({ ...message, now: Date.now() }));
      } catch {
        // Private browsing: other tabs find out on their next request.
      }
    },
    /** Calls `heard` for every other tab's change; returns the unsubscribe. */
    listen(heard: (change: SessionChange) => void) {
      const take = (data: unknown) => {
        const message = data as { change?: SessionChange; tab?: string };
        if (message?.change && message.tab !== tab) heard(message.change);
      };
      if (channel) {
        const onMessage = (event: MessageEvent) => take(event.data);
        channel.addEventListener("message", onMessage);
        return () => channel.removeEventListener("message", onMessage);
      }
      const onStorage = (event: Event) => {
        const { key, newValue } = event as StorageEvent;
        if (key !== NAME || !newValue) return;
        try {
          take(JSON.parse(newValue));
        } catch {
          // Not ours.
        }
      };
      target?.addEventListener("storage", onStorage);
      return () => target?.removeEventListener("storage", onStorage);
    },
    close() {
      channel?.close();
    },
  };
}
