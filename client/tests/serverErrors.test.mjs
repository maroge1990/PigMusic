import assert from "node:assert/strict";
import test from "node:test";

// #239: the server tells a busy server from a broken request.
const events = [];
globalThis.window = {
  addEventListener() {},
  dispatchEvent: (event) => events.push(event.type),
};
const { api, clearSessionReads } = await import("../src/api.ts");

const json = (status, body, headers = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
const busy = (headers = { "Retry-After": "2" }) =>
  json(
    503,
    {
      code: "server_busy",
      detail: "The server is busy. Try again shortly.",
    },
    headers,
  );
const unconfirmed = () =>
  json(503, {
    code: "busy_unconfirmed",
    detail:
      "The server was busy. Check whether this was saved before trying again.",
  });

function answer(...responses) {
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path, method: options?.method || "GET" });
    const next = responses.shift();
    if (!next) throw new Error("unexpected request " + path);
    return next();
  };
  return calls;
}

async function failure(promise) {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  assert.fail("the request should have failed");
}

/** Lets pending promise callbacks run between timer ticks. */
async function settle() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

test.beforeEach(() => {
  clearSessionReads();
  events.length = 0;
});

test("a busy read is asked again after the server's Retry-After and then succeeds", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = answer(
    () => busy({ "Retry-After": "3" }),
    () => json(200, { ok: true }),
  );
  const result = api("/v1/me");
  await settle();
  assert.equal(calls.length, 1);
  t.mock.timers.tick(2999);
  await settle();
  assert.equal(calls.length, 1, "it waits the whole Retry-After");
  t.mock.timers.tick(1);
  await settle();
  assert.equal(calls.length, 2);
  assert.deepEqual(await result, { ok: true });
});

test("a read still busy after two retries says the server is busy", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  // Retry-After is capped at 10 s; a missing one waits 2 s.
  const calls = answer(
    () => busy({ "Retry-After": "60" }),
    () => busy({}),
    () => busy(),
  );
  const result = failure(api("/v1/me"));
  await settle();
  t.mock.timers.tick(10000);
  await settle();
  assert.equal(calls.length, 2);
  t.mock.timers.tick(2000);
  await settle();
  assert.equal(calls.length, 3);
  const error = await result;
  assert.equal(calls.length, 3, "at most two retries");
  assert.equal(error.kind, "busy");
  assert.equal(error.message, "The server is busy. Try again shortly.");
});

test("a proxy that cannot reach the server counts as busy", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const status of [429, 502, 504]) {
    const calls = answer(
      () => new Response("<html>Bad gateway</html>", { status }),
      () => json(200, { status }),
    );
    const result = api("/v1/me");
    await settle();
    t.mock.timers.tick(2000);
    await settle();
    assert.deepEqual(await result, { status });
    assert.equal(calls.length, 2);
  }
  const calls = answer(
    () => new Response("unavailable", { status: 503 }),
    () => new Response("unavailable", { status: 503 }),
    () => new Response("unavailable", { status: 503 }),
  );
  const result = failure(api("/v1/me"));
  await settle();
  t.mock.timers.tick(2000);
  await settle();
  t.mock.timers.tick(2000);
  const error = await result;
  assert.equal(calls.length, 3);
  assert.equal(error.kind, "busy");
  assert.equal(error.message, "The server is busy. Try again shortly.");
});

test("a busy write is never sent again", async () => {
  const calls = answer(busy);
  const error = await failure(
    api("/v1/playlists", { method: "POST", body: "{}" }),
  );
  assert.equal(calls.length, 1);
  assert.equal(error.kind, "busy");
  assert.equal(error.message, "The server is busy. Try again shortly.");
});

test("an unconfirmed write is not re-sent, says it may have been saved and asks open views to read again", async () => {
  const calls = answer(unconfirmed);
  const error = await failure(
    api("/v1/playlists", { method: "POST", body: "{}" }),
  );
  assert.equal(calls.length, 1);
  assert.equal(error.kind, "unconfirmed");
  assert.equal(
    error.message,
    "The server was busy, so this may or may not have been saved. Check before trying again.",
  );
  assert.ok(events.includes("music-library-changed"));
  assert.ok(events.includes("music-requests-changed"));
});

test("a server fault shows its reference and is not retried", async () => {
  const calls = answer(() =>
    json(500, {
      code: "server_error",
      detail: "Something went wrong on the server.",
      request_id: "0123abcd4567ef89",
    }),
  );
  const error = await failure(
    api("/v1/playlists", { method: "POST", body: "{}" }),
  );
  assert.equal(calls.length, 1);
  assert.equal(error.kind, "server");
  assert.equal(
    error.message,
    "That didn't work: something went wrong on the server. Reference 0123abcd",
  );
  const read = answer(() => new Response("oops", { status: 500 }));
  const plain = await failure(api("/v1/me"));
  assert.equal(read.length, 1);
  assert.equal(
    plain.message,
    "That didn't work: something went wrong on the server.",
  );
});

test("a write the proxy gave up on may have been saved; other proxy failures are busy", async () => {
  const calls = answer(() => new Response("Gateway timeout", { status: 504 }));
  const error = await failure(
    api("/v1/playlists", { method: "POST", body: "{}" }),
  );
  assert.equal(calls.length, 1, "never re-sent");
  assert.equal(error.kind, "unconfirmed");
  assert.equal(
    error.message,
    "The server was busy, so this may or may not have been saved. Check before trying again.",
  );
  assert.ok(events.includes("music-library-changed"));
  for (const status of [502, 429, 503]) {
    events.length = 0;
    const sent = answer(() => new Response("unavailable", { status }));
    const busyWrite = await failure(api("/v1/playlists", { method: "DELETE" }));
    assert.equal(sent.length, 1);
    assert.equal(busyWrite.kind, "busy", `status ${status}`);
    assert.deepEqual(events, []);
  }
});

test("a refused request keeps the server's own message", async () => {
  answer(() => json(409, { code: "conflict", detail: "Already there." }));
  const error = await failure(api("/v1/me"));
  assert.equal(error.kind, "request");
  assert.equal(error.code, "conflict");
  assert.equal(error.message, "Already there.");
});
