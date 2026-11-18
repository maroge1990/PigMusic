/** Signing out while the server can't be reached (#80): this device is
 * cleared at once and the session is ended the next time PigMusic opens and
 * reaches the server. The later request ends that one session by its id
 * (DELETE /me/sessions/{id}, the id /me gave); it never touches the sign-in
 * cookie, so a newer sign-in in another tab is unaffected. */

const PENDING = "pig-music-pending-sign-out";
// Signed out offline without knowing the session: stay signed out until the
// person signs in again (ending "whatever the cookie is" could end theirs).
const UNKNOWN = "unknown";

export const OFFLINE_SIGN_OUT =
  "Signed out on this device. PigMusic couldn't reach the server, so it will finish signing out the next time it opens.";

type Call = (
  path: string,
  options?: RequestInit & { anySession?: boolean },
) => Promise<unknown>;

/** How long sign-out waits for pending writes, then for the logout. */
export const SIGN_OUT_WAIT_MS = 3_000;

/** Settles with `promise`, or rejects with a timed-out error after `ms`. */
function within<T>(promise: Promise<T>, ms: number) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([
    promise,
    new Promise<never>((_, reject) => {
      timer = setTimeout(
        () =>
          reject(
            Object.assign(new Error("Sign-out timed out"), {
              code: "timed_out",
            }),
          ),
        ms,
      );
    }),
  ]).finally(() => clearTimeout(timer));
}

function failedCode(error: unknown) {
  return (error as { code?: string } | null)?.code;
}

/** Sign out; true when the server ended the session now, false when it
 * couldn't be reached and the session will be ended later. `flush` first
 * sends every change still waiting for its Undo time, under this session
 * (D-79): after the logout nothing deferred may run. */
export async function signOutSession(
  call: Call,
  session: string | null,
  storage: Pick<Storage, "setItem"> = localStorage,
  drain: () => Promise<unknown> = async () => {},
  wait = SIGN_OUT_WAIT_MS,
): Promise<boolean> {
  // Writes still waiting for their Undo time, or already on their way, go
  // first, under this session (D-79), but never hold sign-out up for long.
  await within(drain(), wait).catch(() => {});
  // No session known (it was already lost): nothing of this tab's to end,
  // and the cookie may be another sign-in's.
  if (!session) return true;
  try {
    await within(
      call("/v1/auth/logout", {
        method: "POST",
        headers: { "X-Pig-Expected-Session": session },
      }),
      wait,
    );
    return true;
  } catch (error) {
    const code = failedCode(error);
    // Signed out already, or the cookie is another sign-in's now: this
    // session is out of this browser's reach either way.
    if (code === "authentication_required" || code === "session_changed")
      return true;
    try {
      storage.setItem(PENDING, session);
    } catch {
      // Private browsing: the session ends when it expires.
    }
    return false;
  }
}

/** End a session left from an offline sign-out. False while it can't be
 * ended yet: the app stays signed out until then. */
export async function finishSignOut(
  call: Call,
  storage: Pick<Storage, "getItem" | "removeItem"> = localStorage,
): Promise<boolean> {
  let pending: string | null = null;
  try {
    pending = storage.getItem(PENDING);
  } catch {
    return true;
  }
  if (!pending) return true;
  if (pending === UNKNOWN) return false;
  try {
    // An earlier sign-in's session, ended by its id: it names no session.
    await call(`/v1/me/sessions/${encodeURIComponent(pending)}`, {
      method: "DELETE",
      anySession: true,
    });
  } catch (error) {
    // Already signed out, or now signed in as someone else, whose sessions
    // can't include it: nothing this browser can still use is left to end.
    const code = failedCode(error);
    if (code !== "authentication_required" && code !== "session_not_found")
      return false;
  }
  try {
    // Another tab may have signed out offline again while this request ran:
    // clear only what was ended, then deal with the newer one (Codex round 2).
    if (storage.getItem(PENDING) !== pending)
      return finishSignOut(call, storage);
    storage.removeItem(PENDING);
  } catch {
    // Nothing to forget.
  }
  return true;
}

/** A fresh sign-in on this device lifts a sign-out whose session was never
 * known; a known one is still ended by id on the next start. */
export function signedInAgain(
  storage: Pick<Storage, "getItem" | "removeItem"> = localStorage,
) {
  try {
    if (storage.getItem(PENDING) === UNKNOWN) storage.removeItem(PENDING);
  } catch {
    // Nothing to lift.
  }
}
