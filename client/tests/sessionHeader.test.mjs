// Codex round 2 (#250, #183): every write names the session it was started
// under, so the server refuses one that another sign-in's cookie carries.
import assert from "node:assert/strict";
import test from "node:test";

const events = [];
globalThis.window = {
  addEventListener() {},
  dispatchEvent: (event) => events.push([event.type, event.detail]),
};
const { setSessionId, withSession } = await import("../src/sessionId.ts");
const { api, apiFetch } = await import("../src/api.ts");

function serve(respond = () => Response.json({})) {
  const sent = [];
  globalThis.fetch = async (path, options) => {
    sent.push([
      options?.method || "GET",
      path,
      new Headers(options?.headers).get("X-Pig-Expected-Session"),
    ]);
    return respond();
  };
  return sent;
}

test("a write names the tab's session; one started earlier names its own", async () => {
  setSessionId("session-1");
  const sent = serve();
  await api("/v1/me/sessions", { method: "DELETE" });
  await api("/v1/people/s/setup-code", {
    method: "POST",
    headers: { "X-Pig-Expected-Session": "session-0" },
  });
  // A deferred send runs bound to the session it was scheduled under.
  await withSession("session-0", () =>
    apiFetch("/api/v1/admin/queue/clear", { method: "POST" }),
  );
  // Signing in is made before there is a session to name.
  await api("/v1/auth/login", { method: "POST", body: "{}" });
  assert.deepEqual(sent, [
    ["DELETE", "/api/v1/me/sessions", "session-1"],
    ["POST", "/api/v1/people/s/setup-code", "session-0"],
    ["POST", "/api/v1/admin/queue/clear", "session-0"],
    ["POST", "/api/v1/auth/login", null],
  ]);
});

test("with no session known, a write never leaves the tab; a read does", async () => {
  setSessionId(null);
  const sent = serve();
  await assert.rejects(api("/v1/me/sessions", { method: "DELETE" }), {
    code: "session_unknown",
    message: "Couldn't tell who is signed in. Reload PigMusic and try again.",
  });
  await api("/v1/me");
  assert.deepEqual(sent, [["GET", "/api/v1/me", null]]);
});

test("a write refused as another sign-in's says so and ends this session", async () => {
  setSessionId("session-1");
  events.length = 0;
  serve(() =>
    Response.json(
      {
        code: "session_changed",
        detail: "This was started by another sign-in.",
      },
      { status: 409 },
    ),
  );
  await assert.rejects(api("/v1/me/sessions", { method: "DELETE" }), {
    code: "session_changed",
    message: "This was started by another sign-in.",
  });
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(events, [
    ["music-session-changed", "This was started by another sign-in."],
  ]);
});

test("signed out, every write the server takes before sign-in is sent (Codex round 3)", async () => {
  // Activating an invite or a password reset (/v1/setup) included.
  const { PUBLIC_WRITES } = await import("../src/publicWrites.ts");
  setSessionId(null);
  const sent = serve();
  for (const write of PUBLIC_WRITES) {
    const [method, path] = write.split(" ");
    await apiFetch(path, { method, body: "{}" });
  }
  assert.deepEqual(
    sent.map(([method, path, session]) => [`${method} ${path}`, session]),
    [...PUBLIC_WRITES].map((write) => [write, null]),
  );
});

test("a refusal that arrives after the session changed ends nothing", async () => {
  // Codex: a delayed 409 from the previous account tore down the new one.
  const { clearSessionReads } = await import("../src/api.ts");
  globalThis.navigator ??= {};
  setSessionId("session-1");
  events.length = 0;
  let release;
  globalThis.fetch = async () => {
    await new Promise((r) => (release = r));
    return Response.json(
      { code: "session_changed", detail: "Started by another sign-in." },
      { status: 409 },
    );
  };
  const write = api("/v1/me/sessions", { method: "DELETE" }).catch(() => {});
  while (!release) await new Promise((r) => setImmediate(r));
  clearSessionReads();
  setSessionId("session-2");
  events.length = 0;
  release();
  await write;
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(
    events.filter(([type]) => type === "music-session-changed"),
    [],
  );
});
