/** A structured error body has to survive the one fetch wrapper every endpoint goes through.
 *
 * `request()` lifts `detail` into the thrown message only when it is a string,
 * so a 409 whose detail is an object — `{error, expected_revision,
 * current_revision, message}` — reached the caller as raw JSON text in
 * `message` and nothing else. The ticket pane would then have to re-parse a
 * string the wrapper had already parsed, in a component, to recover a number.
 *
 * So `ApiError` carries the parsed body as well. The string-detail behaviour is
 * unchanged: these tests pin both, because the second is what every other
 * endpoint in the app depends on.
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

describe("request() on an error response", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("carries the parsed body, so an object detail survives the wrapper", async () => {
    const body = {
      detail: {
        error: "revision_conflict",
        expected_revision: 5,
        current_revision: 7,
        message: "This ticket moved on: you edited revision 5 and it is now 7.",
      },
    };
    respondWith(409, JSON.stringify(body));

    const error = await thrownBy(() => request("/api/tickets/t-1", { method: "PATCH" }));

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    // The whole parsed body, not a hand-picked field: the wrapper is not the
    // place that decides which keys an endpoint's error carries.
    expect(payloadOf(error)).toEqual(body);
  });

  it("still puts a string detail in the message, byte for byte", async () => {
    respondWith(400, JSON.stringify({ detail: "Title cannot be empty" }), "Bad Request");

    const error = await thrownBy(() => request("/api/tickets/t-1", { method: "PATCH" }));

    expect((error as ApiError).message).toBe("Title cannot be empty");
    expect((error as ApiError).status).toBe(400);
  });

  it("falls back to the raw text when the body is not JSON at all", async () => {
    respondWith(502, "<html>upstream died</html>", "Bad Gateway");

    const error = await thrownBy(() => request("/api/tickets/t-1"));

    expect((error as ApiError).message).toBe("<html>upstream died</html>");
    expect(payloadOf(error) ?? null).toBeNull();
  });

  it("falls back to the status text when there is no body", async () => {
    respondWith(500, "", "Internal Server Error");

    const error = await thrownBy(() => request("/api/tickets/t-1"));

    expect((error as ApiError).message).toBe("Internal Server Error");
  });
});
