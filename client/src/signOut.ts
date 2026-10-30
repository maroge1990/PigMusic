/** Signing out while the server can't be reached (#80): this device is
 * cleared at once and the session is ended the next time PigMusic opens and
 * reaches the server. The later request ends that one session by its id
 * (DELETE /me/sessions/{id}); it never touches the sign-in cookie, so a
 * newer sign-in in another tab is unaffected. */

const PENDING = "pig-music-pending-sign-out";

export const OFFLINE_SIGN_OUT =
  "Signed out on this device. PigMusic couldn't reach the server, so it will finish signing out the next time it opens.";

type Call = (path: string, options?: RequestInit) => Promise<unknown>;

function failedCode(error: unknown) {
  return (error as { code?: string } | null)?.code;
}

/** The current session's id while online, so it can be ended later. */
export async function currentSession(call: Call): Promise<string | null> {
  try {
    const { sessions } = (await call("/v1/me/sessions")) as {
      sessions: { id: string; current: boolean }[];
    };
    return sessions.find((s) => s.current)?.id ?? null;
  } catch {
    return null; // Only needed to sign out while offline.
  }
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
      if (session) storage.setItem(PENDING, session);
    } catch {
      // Private browsing: the session ends when it expires.
    }
    return false;
  }
}

/** End a session left from an offline sign-out. False while the server
 * still can't be reached: the app stays signed out until it can. */
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
  try {
    await call(`/v1/me/sessions/${encodeURIComponent(pending)}`, {
      method: "DELETE",
    });
  } catch (error) {
    // Signed out already (or never signed in again): nothing left to end.
    if (failedCode(error) !== "authentication_required") return false;
  }
  try {
    storage.removeItem(PENDING);
  } catch {
    // Nothing to forget.
  }
  return true;
}
