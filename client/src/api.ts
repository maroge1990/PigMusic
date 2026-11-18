import { PUBLIC_WRITES } from "./publicWrites";
import {
  SESSION_HEADER,
  SESSION_UNKNOWN,
  expectedSession,
  setSessionId,
} from "./sessionId";
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

/** A request; `anySession` sends a write without naming a session (ending
 * an old session by its id after an offline sign-out). */
export type ApiInit = RequestInit & { anySession?: boolean };

export async function apiFetch(
  path: string,
  init?: ApiInit,
): Promise<Response> {
  const { anySession, ...rest } = init ?? {};
  let options: RequestInit | undefined = init && rest;
  // U01-4: only the versioned API exists; an old path is a client bug.
  if (!path.startsWith("/api/v1/"))
    throw new Error(`Unversioned API path ${path.split("?")[0]}`);
  const method = options?.method?.toUpperCase() || "GET";
  const headers = new Headers(options?.headers);
  if (method !== "GET" && method !== "HEAD") {
    headers.set("X-Pig-Request", "1");
    // Every write names the session it was started under (#250).
    if (
      !headers.has(SESSION_HEADER) &&
      !anySession &&
      // Writes the server takes before sign-in name no session.
      !PUBLIC_WRITES.has(`${method} ${path.split("?")[0]}`)
    ) {
      const session = expectedSession();
      if (!session)
        throw new ApiError(SESSION_UNKNOWN, "session_unknown", {}, "request");
      headers.set(SESSION_HEADER, session);
    }
  }
  options = { ...options, headers, credentials: "same-origin" };

  if (method !== "GET" || options?.signal) {
    const response = await readResponse(path, options);
    if (response.status === 409)
      // Another sign-in holds the cookie now: the write was refused unmade.
      void response
        .clone()
        .json()
        .then(
          (body) =>
            body?.code === "session_changed" &&
            window.dispatchEvent(
              new CustomEvent("music-session-changed", { detail: body.detail }),
            ),
          () => {},
        );
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
    } else if (
      !isRead(method) &&
      !readOnlyPost(path, options) &&
      (await unconfirmedWrite(response))
    ) {
      // The write may have been saved: drop what was read before it and let
      // open views read again, so the person can see whether it was (#239).
      reads.clear();
      libraryRead = undefined;
      window.dispatchEvent(new Event("music-library-changed"));
      window.dispatchEvent(new Event("music-requests-changed"));
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

/** What kind of failure a response was (#239): the server busy or out of
 * reach (`busy`), a write that may or may not have been saved
 * (`unconfirmed`), an unexpected server fault (`server`), or the server
 * refusing this request (`request`). */
export type FailureKind = "busy" | "unconfirmed" | "server" | "request";

export const BUSY_MESSAGE = "The server is busy. Try again shortly.";
export const UNCONFIRMED_MESSAGE =
  "The server was busy, so this may or may not have been saved. Check before trying again.";
export const SERVER_ERROR_MESSAGE =
  "That didn't work: something went wrong on the server.";

export function failureKind(
  status: number,
  code?: unknown,
  write = false,
): FailureKind {
  if (status === 503 && code === "busy_unconfirmed") return "unconfirmed";
  // The proxy gave up waiting; the server may still have saved the write.
  if (status === 504 && write) return "unconfirmed";
  if (
    (status === 503 && (code === "server_busy" || !code)) ||
    status === 429 ||
    status === 502 ||
    status === 504
  )
    return "busy";
  if (status === 500) return "server";
  return "request";
}

export class ApiError extends Error {
  public code: string;
  public body: Record<string, unknown>;
  public kind: FailureKind;
  constructor(
    message: string,
    code: string,
    body: Record<string, unknown> = {},
    kind: FailureKind = "request",
  ) {
    super(message);
    this.code = code;
    this.body = body;
    this.kind = kind;
  }
}

/** A write the server could not confirm: it may have been saved. */
export function isUnconfirmed(error: unknown) {
  return error instanceof ApiError && error.kind === "unconfirmed";
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

export async function api<T>(path: string, options?: ApiInit): Promise<T> {
  const response = await apiFetch("/api" + path, options);
  if (!response.ok)
    throw await responseError(
      response,
      !isRead(options?.method?.toUpperCase() || "GET"),
    );
  return response.json();
}

/** Turns a failed response into the error a view shows: the server's own
 * message, or one of the server-condition messages (#239). */
export async function responseError(
  response: Response,
  write = false,
): Promise<ApiError> {
  let message = "Something went wrong. Please try again.";
  let code = "request_failed";
  let details: Record<string, unknown> = {};
  try {
    const body = await response.clone().json();
    if (body && typeof body === "object" && !Array.isArray(body)) {
      details = body;
      if (typeof body.code === "string" && body.code) code = body.code;
      if (typeof body.detail === "string")
        message = accountErrors[code] || body.detail;
    }
  } catch {
    /* Keep a readable response for non-JSON failures. */
  }
  const kind = failureKind(response.status, details.code, write);
  if (kind === "busy") message = accountErrors[code] || BUSY_MESSAGE;
  else if (kind === "unconfirmed") message = UNCONFIRMED_MESSAGE;
  else if (kind === "server") {
    const id = details.request_id;
    message =
      typeof id === "string" && id
        ? `${SERVER_ERROR_MESSAGE} Reference ${id.slice(0, 8)}`
        : SERVER_ERROR_MESSAGE;
  }
  return new ApiError(message, code, details, kind);
}

/** The error for a failed write sent with `apiFetch` (#239). Only a refusal
 * (the server said no to this request) keeps the view's own wording; a busy
 * server, an unconfirmed write or a server fault always says what happened.
 * `refused` is the view's wording, or a function choosing it (undefined
 * keeps the server's message). */
export async function writeFailure(
  response: Response,
  refused?: string | ((error: ApiError) => string | undefined),
  /** False for a POST that only reads (playback): a 504 is then busy. */
  write = true,
): Promise<ApiError> {
  const error = await responseError(response, write);
  if (error.kind !== "request" || refused === undefined) return error;
  const wording = typeof refused === "string" ? refused : refused(error);
  if (wording) error.message = wording;
  return error;
}

/** A refusal shows the server's sentence, or `fallback` when it sent none. */
export const detailOr = (fallback: string) => (error: ApiError) =>
  typeof error.body.detail === "string" ? undefined : fallback;

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
  // Until /v1/me answers again, no write can say whose it is.
  setSessionId(null);
  window.dispatchEvent(new Event("music-session-cleared"));
}

const BUSY_RETRIES = 2;
const DEFAULT_RETRY_MS = 2000;
const MAX_RETRY_MS = 10000;

function isRead(method: string) {
  return method === "GET" || method === "HEAD" || method === "OPTIONS";
}

/** The server or its proxy answered that it is busy or out of reach. */
async function busyResponse(response: Response) {
  if (![429, 502, 503, 504].includes(response.status)) return false;
  let code: unknown;
  if (response.status === 503)
    try {
      code = (await response.clone().json())?.code;
    } catch {
      /* A proxy's own page: no code. */
    }
  return failureKind(response.status, code) === "busy";
}

function retryDelay(response: Response) {
  const header = response.headers.get("Retry-After");
  const seconds = header === null ? NaN : Number(header.trim());
  if (!header?.trim() || !Number.isFinite(seconds) || seconds < 0)
    return DEFAULT_RETRY_MS;
  return Math.min(seconds * 1000, MAX_RETRY_MS);
}

function pause(ms: number, signal?: AbortSignal | null) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, ms);
    if (signal?.aborted) return abort();
    signal?.addEventListener("abort", abort, { once: true });
  });
}

/** A read the server was too busy for is asked again, at most twice, after
 * the wait it named (#239). Writes are never sent twice: the first may have
 * been saved. */
async function fetchRetrying(path: string, options?: RequestInit) {
  const method = options?.method?.toUpperCase() || "GET";
  const generation = sessionGeneration;
  let response = await fetch(path, options);
  for (let retry = 0; retry < BUSY_RETRIES && isRead(method); retry++) {
    if (!(await busyResponse(response))) break;
    await pause(retryDelay(response), options?.signal);
    // A read for the person who just left is not repeated.
    if (generation !== sessionGeneration) break;
    response = await fetch(path, options);
  }
  return response;
}

/** The server was busy while handling a write and cannot say whether it was
 * saved (the code is only sent for writes), or the proxy gave up waiting. */
async function unconfirmedWrite(response: Response) {
  if (response.status === 504) return true;
  if (response.status !== 503) return false;
  try {
    return (await response.clone().json())?.code === "busy_unconfirmed";
  } catch {
    return false;
  }
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
  const response = await fetchRetrying(path, options);
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
