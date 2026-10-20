/** The shapes a failed response actually arrives in.
 *
 * AC14 of lg-milestone-that-1239. `httpErrorBody.test.ts` pins the happy
 * refusal, a 422 and a non-JSON body. This file attacks the parse: the bodies
 * that are JSON but not an object, the ones that are an object with no string
 * `detail`, and the mock-shaped success — a `request()` that reads the body
 * twice passes a test whose fake response allows it and throws
 * `TypeError: Body is unusable` in a browser, where a `Response` can be
 * consumed exactly once.
 */

import { ApiError, request } from "../http";

/** A response that counts its reads, the way a real one enforces them. */
function respondWith(body: string, { status = 409, ok = false } = {}) {
  const text = jest.fn().mockResolvedValue(body);
  const json = jest.fn().mockImplementation(() => Promise.resolve(JSON.parse(body)));
  global.fetch = jest.fn().mockResolvedValue({
    ok,
    status,
    statusText: "Conflict",
    text,
    json,
  }) as unknown as typeof fetch;
  return { text, json };
}

async function errorFrom(call: Promise<unknown>): Promise<ApiError> {
  return call.then(
    () => {
      throw new Error("the request resolved; it was supposed to reject");
    },
    (err: unknown) => {
      expect(err).toBeInstanceOf(ApiError);
      return err as ApiError;
    },
  );
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe("request() reads a failed body once", () => {
  it("parses the body it already read rather than reading the response twice", async () => {
    // A real `Response` throws on the second read. Both `text()` then `json()`
    // and `json()` then `text()` are fine against this mock and broken in the
    // app, which is the whole reason this assertion is on the call counts.
    const { text, json } = respondWith(JSON.stringify({ detail: "no", references: {} }));

    await errorFrom(request("/api/studio/agents/planner", { method: "DELETE" }));

    expect(text.mock.calls.length + json.mock.calls.length).toBe(1);
  });
});

describe("request() on a JSON body that is not the expected shape", () => {
  it("keeps a usable message when detail is a list rather than a string", async () => {
    // FastAPI's own 422 shape. AC9 is why the slug rejection does not use it,
    // but any other validation error in the app still arrives like this, and
    // `[object Object]` in a toast reports nothing.
    const body = {
      detail: [{ type: "extra_forbidden", loc: ["body", "slug"], msg: "Extra inputs" }],
    };
    respondWith(JSON.stringify(body), { status: 422 });

    const error = await errorFrom(request("/api/studio/agents/planner", { method: "PATCH" }));

    expect(error.status).toBe(422);
    expect(error.body).toEqual(body);
    expect(typeof error.message).toBe("string");
    expect(error.message).not.toContain("[object Object]");
    expect(error.message.length).toBeGreaterThan(0);
  });

  it("carries a body with no detail at all", async () => {
    const body = { references: { blocking: [], historical: [] } };
    respondWith(JSON.stringify(body));

    const error = await errorFrom(request("/api/studio/agents/planner", { method: "DELETE" }));

    expect(error.body).toEqual(body);
    expect(error.message.length).toBeGreaterThan(0);
  });

  it.each([
    ["a JSON array", "[1, 2, 3]"],
    ["a JSON string", '"just a string"'],
    ["a JSON number", "7"],
    ["JSON null", "null"],
  ])("does not throw while reading %s", async (_label, raw) => {
    respondWith(raw, { status: 500 });

    const error = await errorFrom(request("/api/studio/agents/planner", { method: "DELETE" }));

    expect(error.status).toBe(500);
    expect(error.message.length).toBeGreaterThan(0);
  });

  it("leaves an empty body with the status line and no parsed body", async () => {
    respondWith("", { status: 502 });

    const error = await errorFrom(request("/api/studio/agents/planner", { method: "DELETE" }));

    expect(error.status).toBe(502);
    expect(error.message).toBe("Conflict"); // the mock's statusText
    expect(error.body).toBeUndefined();
  });

  it("leaves a truncated JSON body with the raw text and no parsed body", async () => {
    // A connection cut mid-response. The parse fails, and the existing
    // `silent-ok` fallback is the alternate path: the raw text is the message.
    respondWith('{"detail": "Cannot delete agen', { status: 409 });

    const error = await errorFrom(request("/api/studio/agents/planner", { method: "DELETE" }));

    expect(error.message).toBe('{"detail": "Cannot delete agen');
    expect(error.body).toBeUndefined();
  });

  it("carries the body on any failing status, not only the refusal's 409", async () => {
    // The notice reads `body`, but nothing about the field is 409-specific; a
    // parse keyed on the status would drop the 422's sentence (AC9).
    const body = { detail: "slug cannot be changed after creation" };
    respondWith(JSON.stringify(body), { status: 422 });

    const error = await errorFrom(request("/api/studio/agents/planner", { method: "PATCH" }));

    expect(error.body).toEqual(body);
    expect(error.message).toBe(body.detail);
  });
});
