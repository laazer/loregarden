/** The fetch wrapper's new `payload`, on the bodies it will actually be handed.
 *
 * `httpErrorPayload.test.ts` pins the case the ticket is about: a 409 whose
 * `detail` is an object. This file is about the bodies around it — a string
 * detail, a body with no `detail` at all, a JSON scalar, a JSON array — because
 * `payload` is added to the one wrapper every endpoint in the app throws
 * through, and the cheap implementation ("parse it and keep it, but only in the
 * branch where the detail is an object") is indistinguishable from the correct
 * one until something sends a body of a different shape.
 *
 * The question each test answers is the same: did adding `payload` change what
 * `message` has always been?
 */

import { ApiError, request } from "../http";

function payloadOf(error: unknown): unknown {
  return (error as { payload?: unknown }).payload;
}

function respondWith(status: number, body: string, statusText = "Conflict") {
  global.fetch = jest.fn().mockResolvedValue({
    ok: false,
    status,
    statusText,
    text: async () => body,
  });
}

async function thrownBy(call: () => Promise<unknown>): Promise<unknown> {
  try {
    await call();
  } catch (error) {
    return error;
  }
  throw new Error("the request resolved; it was expected to throw");
}

const patch = () => request("/api/tickets/t-1", { method: "PATCH" });

describe("the parsed body on an error the wrapper has always handled", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("carries the body for a string detail too, beside the unchanged message", async () => {
    // The structured 409 is one endpoint's error shape. Keeping the parsed body
    // only for that shape makes `payload` mean "this was a revision conflict",
    // which is not what it is called or where it lives.
    const body = { detail: "Title cannot be empty" };
    respondWith(400, JSON.stringify(body), "Bad Request");

    const error = await thrownBy(patch);

    expect((error as ApiError).message).toBe("Title cannot be empty");
    expect(payloadOf(error)).toEqual(body);
  });

  it("carries a JSON body that has no detail at all", async () => {
    // FastAPI's validation errors, and anything proxied from another service,
    // arrive like this. The message falls back to the raw text, as it always has.
    const body = { errors: [{ loc: ["body", "expected_revision"], msg: "not an integer" }] };
    respondWith(422, JSON.stringify(body), "Unprocessable Entity");

    const error = await thrownBy(patch);

    expect((error as ApiError).message).toBe(JSON.stringify(body));
    expect(payloadOf(error)).toEqual(body);
  });

  it("leaves the message alone when the detail is an object", async () => {
    // The pane reads `payload`; `describeError` still reads `message`, and a
    // 409 that reached a toast as "[object Object]" would be a regression in
    // every other surface that catches one.
    const body = { detail: { error: "revision_conflict", current_revision: 7 } };
    respondWith(409, JSON.stringify(body));

    const error = await thrownBy(patch);

    expect((error as ApiError).message).toBe(JSON.stringify(body));
    expect((error as ApiError).status).toBe(409);
  });

  it.each([
    ["a JSON null", "null"],
    ["a JSON number", "42"],
    ["a JSON string", '"just a sentence"'],
    ["a JSON array", '[{"detail":"nested"}]'],
  ])("does not throw while reading %s as a body", async (_label, body) => {
    // Every one of these parses, and none of them has a `detail` the wrapper can
    // lift — `null.detail` being the one that throws inside the catch-free path.
    // What a caller gets back must still be an ApiError carrying the status.
    respondWith(409, body);

    const error = await thrownBy(patch);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).message).toBe(body);
  });

  it("reports a 204-shaped empty body as the status text, with no payload", async () => {
    respondWith(503, "", "Service Unavailable");

    const error = await thrownBy(patch);

    expect((error as ApiError).message).toBe("Service Unavailable");
    expect(payloadOf(error) ?? null).toBeNull();
  });
});
