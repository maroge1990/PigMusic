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

type Call = (path: string, options?: RequestInit) => Promise<unknown>;

function failedCode(error: unknown) {
  return (error as { code?: string } | null)?.code;
}

/** Sign out; true when the server ended the session now, false when it
 * couldn't be reached and the session will be ended later. */
export async function signOutSession(
  call: Call,
  session: string | null,
  storage: Pick<Storage, "setItem"> = localStorage,
): Promise<boolean> {
  try {
    await call("/v1/auth/logout", { method: "POST" });
    return true;
  } catch (error) {
    if (failedCode(error) === "authentication_required") return true;
    try {
      storage.setItem(PENDING, session || UNKNOWN);
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
    await call(`/v1/me/sessions/${encodeURIComponent(pending)}`, {
      method: "DELETE",
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
