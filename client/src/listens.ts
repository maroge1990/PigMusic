import { apiFetch } from "./api";

/** A listen, counted like iPhone: once per play, after 30 seconds of audio. */
export type Listen = {
  event_id: string;
  track_id: string;
  played_at: number;
  seconds: number;
};

export const LISTEN_SECONDS = 30;
const KEY = "pig-music-listens";
const MONTH = 30 * 86400;

function read(): Listen[] {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function write(listens: Listen[]) {
  try {
    if (listens.length) localStorage.setItem(KEY, JSON.stringify(listens));
    else localStorage.removeItem(KEY);
  } catch {
    /* Storage unavailable: the listen is still sent now. */
  }
}

let sending = false;

/** Keep the listen until the server confirms it; event ids make resending safe. */
export function recordListen(listen: Listen) {
  const now = Math.floor(Date.now() / 1000);
  const kept = read().filter(
    (l) => l.played_at >= now - MONTH && l.event_id !== listen.event_id,
  );
  write([...kept, listen].slice(-500));
  void flushListens();
}

/** Worth resending later: signed out, refused for now, or rate-limited. Any
 * other 4xx refuses the batch as sent; the server already skips a listen it
 * can't record, so resending it would block every later listen (#66). */
const RETRY_LATER = new Set([401, 403, 408, 425, 429]);

function settled(status: number) {
  return status < 500 && !RETRY_LATER.has(status);
}

export async function flushListens() {
  if (sending) return;
  const now = Math.floor(Date.now() / 1000);
  const listens = read();
  const fresh = listens.filter((l) => l.played_at >= now - MONTH);
  if (fresh.length !== listens.length) write(fresh);
  const pending = fresh.slice(0, 200);
  if (!pending.length) return;
  sending = true;
  try {
    const response = await apiFetch("/api/v1/play-events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events: pending }),
      keepalive: true,
    });
    if (settled(response.status)) {
      const sent = new Set(pending.map((l) => l.event_id));
      write(read().filter((l) => !sent.has(l.event_id)));
    }
  } catch {
    /* Offline: retried on the next listen, reconnect or visit. */
  } finally {
    sending = false;
  }
}

/** Another account must never send this browser's earlier listens. */
export function forgetListens() {
  write([]);
}
