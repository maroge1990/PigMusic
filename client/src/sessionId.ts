/**
 * The server's id for the session this tab is signed in with (/v1/me's
 * session_id). Every write names the session it was started under in
 * `SESSION_HEADER`, and the server refuses one whose session is no longer
 * the cookie's (another tab signed in): client-side checks alone race the
 * shared cookie (#250).
 */
export const SESSION_HEADER = "X-Pig-Expected-Session";
export const SESSION_UNKNOWN =
  "Couldn't tell who is signed in. Reload PigMusic and try again.";

let current: string | null = null;
let pinned: string | null | undefined;

/** Set once /v1/me answers; null while the session is lost or unknown. */
export function setSessionId(id: string | null) {
  current = id;
}

/** The session a write started now belongs to: the one a deferred send was
 * scheduled under while it runs, else the tab's current one. */
export function expectedSession(): string | null {
  return pinned === undefined ? current : pinned;
}

/** Runs `start`, binding the writes it begins (synchronously) to `id`. */
export function withSession<T>(id: string | null, start: () => T): T {
  const before = pinned;
  pinned = id;
  try {
    return start();
  } finally {
    pinned = before;
  }
}

/** The header naming `id`, for a write started earlier than it is sent. */
export function sessionHeader(id: string): Record<string, string> {
  return { [SESSION_HEADER]: id };
}
