/** Shared transport: deduplicate concurrent reads, clone responses for callers,
 * and invalidate reads after successful mutations. No browser credential cache.
 */
const reads = new Map<string, Promise<Response>>();
let librarySnapshot: unknown;
const cataloguePages = new Map<string, { value: unknown; time: number }>();
export function peekLibrary<T>() {
  return librarySnapshot as T | undefined;
}
export function peekCatalogue<T>(path: string): T | undefined {
  const hit = cataloguePages.get(path);
  return hit && Date.now() - hit.time < 60000 ? (hit.value as T) : undefined;
}

let libraryRead: Promise<unknown> | undefined;
const libraryResponses = new Map<string, Response>();
let sessionGeneration = 0;
const sessionMaps = new Set<Map<unknown, unknown>>();

/** The signed-in session's number; a read started under an older one must
 * not be kept. */
export function currentSession() {
  return sessionGeneration;
}

/** A map that belongs to the signed-in person: emptied on every account
 * change, together with the shared reads. */
export function sessionMap<K, V>() {
  const map = new Map<K, V>();
  sessionMaps.add(map as Map<unknown, unknown>);
  return map;
}
let artworkRevision: number | undefined;

export async function apiFetch(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  // U01-4: only the versioned API exists; an old path is a client bug.
  if (!path.startsWith("/api/v1/"))
    throw new Error(`Unversioned API path ${path.split("?")[0]}`);
  const method = options?.method?.toUpperCase() || "GET";
  const headers = new Headers(options?.headers);
  if (method !== "GET" && method !== "HEAD") headers.set("X-Pig-Request", "1");
  options = { ...options, headers, credentials: "same-origin" };

  if (method !== "GET" || options?.signal) {
    const response = await readResponse(path, options);
    if (
      response.ok &&
      method !== "GET" &&
      !readOnlyPost(path, options) &&
      // A favourite status probe reads state; it must not invalidate reads.
      !(
        path === "/api/v1/favourites" &&
        options?.body &&
        !String(options.body).includes('"favourite":')
      )
    ) {
      reads.clear();
      libraryRead = undefined;
    }
    return response;
  }
  let read = reads.get(path);
  if (!read) {
    read = readResponse(path, options);
    reads.set(path, read);
    const current = read;
    void read
      .finally(() => {
        if (reads.get(path) === current) reads.delete(path);
      })
      .catch(() => {});
  }
  return (await read).clone();
}

/** POSTs that only read: starting playback or checking library membership
 * must not throw away pre-cached pages. */
function readOnlyPost(path: string, options?: RequestInit) {
  return (
    options?.method?.toUpperCase() === "POST" &&
    (path === "/api/v1/library/lookup" ||
      path === "/api/v1/remote-playback/catalogue" ||
      path === "/api/v1/remote-playback" ||
      /^\/api\/v1\/playback\/[^/]+\/url$/.test(path))
  );
}

export class ApiError extends Error {
  public code: string;
  public body: Record<string, unknown>;
  constructor(
    message: string,
    code: string,
    body: Record<string, unknown> = {},
  ) {
    super(message);
    this.code = code;
    this.body = body;
  }
}
const accountErrors: Record<string, string> = {
  invalid_credentials: "The username or password is incorrect.",
  invalid_installation_code:
    "The installation code is incorrect. Copy it from PigMusic’s Unraid logs.",
  owner_already_exists:
    "An administrator account already exists. Reload PigMusic to sign in.",
  invalid_username:
    "Use 2–64 letters, numbers, dots, underscores or hyphens for your username.",
  password_not_accepted:
    "Choose a password of 12–256 characters that is not a common password or your username.",
  authentication_required: "Please sign in to continue.",
  setup_required: "The administrator needs to finish the first account setup.",
  https_required: "Open PigMusic using its private HTTPS address.",
  rate_limited: "Too many attempts. Please wait before trying again.",
  invalid_setup_code:
    "The setup code has expired or was already used. Ask your administrator for a new code.",
  username_unavailable: "That username is already in use.",
  weak_password: "Choose a password between 12 and 256 characters.",
};

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch("/api" + path, options);
  if (!response.ok) {
    let message = "Something went wrong. Please try again.";
    let code = "request_failed";
    let details: Record<string, unknown> = {};
    try {
      const body = await response.json();
      details = body;
      code = body.code || "request_failed";
      if (typeof body.detail === "string")
        message = accountErrors[code] || body.detail;
    } catch {
      /* Keep a readable response for non-JSON failures. */
    }
    throw new ApiError(message, code, details);
  }
  return response.json();
}

export function cachedLibrary<T>(): Promise<T> {
  const generation = sessionGeneration;
  libraryRead ||= api("/v1/library")
    .then((library) => {
      if (generation === sessionGeneration) librarySnapshot = library;
      return library;
    })
    .catch((error) => {
      libraryRead = undefined;
      throw error;
    });
  return libraryRead as Promise<T>;
}

function rememberLibrary(library: unknown) {
  librarySnapshot = library;
  libraryRead = Promise.resolve(library);
}

window.addEventListener("music-library-changed", () => {
  libraryRead = undefined;
  // Release lists say which albums this person owns.
  for (const path of [...cataloguePages.keys()])
    if (/\/releases(\?|$)/.test(path)) cataloguePages.delete(path);
});

async function readResponse(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  if (
    !["/api/v1/admin/library", "/api/v1/library"].includes(path) ||
    (options?.method && options.method !== "GET")
  )
    return transport(path, options);
  const generation = sessionGeneration;
  const remembered = libraryResponses.get(path);
  const headers = new Headers(options?.headers);
  const etag = remembered?.headers.get("etag");
  if (etag) headers.set("If-None-Match", etag);
  const response = await transport(path, { ...options, headers });
  if (response.status === 304 && remembered && generation === sessionGeneration)
    return remembered.clone();
  if (response.ok && generation === sessionGeneration) {
    libraryResponses.set(path, response.clone());
    if (path === "/api/v1/library") {
      const library = await response.clone().json();
      if (generation === sessionGeneration) rememberLibrary(library);
    }
  }
  return response;
}

export function updateArtworkRevision(revision: number | undefined) {
  if (revision === undefined || revision === artworkRevision) return;
  artworkRevision = revision;
  window.dispatchEvent(new Event("music-artwork-changed"));
}

// One retry clock for missing images; successful immutable images stay cached.
setInterval(() => {
  if (!document.hidden) window.dispatchEvent(new Event("music-artwork-retry"));
}, 60000);

export function clearSessionReads() {
  // Cached artwork and pages belong to the person who just left.
  globalThis.navigator?.serviceWorker?.controller?.postMessage({
    type: "clear",
  });
  reads.clear();
  libraryRead = undefined;
  libraryResponses.clear();
  librarySnapshot = undefined;
  cataloguePages.clear();
  for (const map of sessionMaps) map.clear();
  ++sessionGeneration;
  window.dispatchEvent(new Event("music-session-cleared"));
}

async function transport(path: string, options?: RequestInit) {
  const generation = sessionGeneration;
  const catalogue =
    (!options?.method || options.method === "GET") &&
    /^\/api\/v1\/catalogue\/artists\/[^/]+\/(popularity|releases)(\?|$)/.test(
      path,
    );
  if (options?.signal?.aborted) throw new DOMException("Aborted", "AbortError");
  const hit = catalogue && peekCatalogue(path);
  if (hit)
    return new Response(JSON.stringify(hit), {
      headers: { "Content-Type": "application/json" },
    });
  const response = await fetch(path, options);
  if (catalogue && response.ok && generation === sessionGeneration) {
    const value = await response.clone().json();
    if (generation === sessionGeneration) {
      if (cataloguePages.size >= 80)
        cataloguePages.delete(cataloguePages.keys().next().value!);
      cataloguePages.set(path, { value, time: Date.now() });
    }
  }
  if (response.status === 401 && generation === sessionGeneration) {
    clearSessionReads();
    window.dispatchEvent(new Event("music-auth-required"));
  }
  if (response.status === 403 && generation === sessionGeneration)
    window.dispatchEvent(
      new CustomEvent("music-forbidden", {
        detail: "You do not have access to that action.",
      }),
    );
  return response;
}
