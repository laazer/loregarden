/** What a failed request carries back, and why the parsed body has to ride along.
 *
 * AC14 of lg-milestone-that-1239. The delete refusal answers
 * `{"detail": "<sentence>", "references": {...}}`: the sentence goes to the
 * toast, and the agent editor's refusal notice needs the structured half to
 * build its links. `ApiError` carried only `status` and `message`, so the
 * notice's only other sources were a second `GET …/references` — the loading
 * state AC12 says must not be added — or parsing identifiers back out of the
 * sentence.
 */

import { ApiError, request } from "../http";

function respondWith(body: string, { status = 409, ok = false } = {}) {
  global.fetch = jest.fn().mockResolvedValue({
    ok,
    status,
    statusText: "Conflict",
    text: () => Promise.resolve(body),
    json: () => Promise.resolve(JSON.parse(body)),
  }) as unknown as typeof fetch;
}

async function errorFrom(call: Promise<unknown>): Promise<ApiError> {
  await expect(call).rejects.toBeInstanceOf(ApiError);
  return call.then(
    () => {
      throw new Error("the request resolved; it was supposed to reject");
    },
    (err: ApiError) => err,
  );
}

const REFUSAL = {
  detail:
    "Cannot delete agent 'planner': 8 workflow template(s) still name it (studio-tdd-v3, studio-blobert-ddd).",
  references: {
    blocking: [
      { kind: "workflow_template", ids: ["studio-tdd-v3", "studio-blobert-ddd"], count: 8 },
    ],
    historical: [{ kind: "agent_run", ids: [], count: 491 }],
  },
};

describe("request() on a failing response", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("carries the parsed JSON error body on the thrown ApiError", async () => {
    respondWith(JSON.stringify(REFUSAL));

    const error = await errorFrom(request("/api/studio/agents/planner", { method: "DELETE" }));

    expect(error.payload).toEqual(REFUSAL);
  });

  it("still lifts a string detail into the message", async () => {
    respondWith(JSON.stringify(REFUSAL));

    const error = await errorFrom(request("/api/studio/agents/planner", { method: "DELETE" }));

    expect(error.message).toBe(REFUSAL.detail);
    expect(error.status).toBe(409);
  });

  it("leaves a non-JSON body with today's message, status and no body", async () => {
    respondWith("<html>502 Bad Gateway</html>", { status: 502 });

    const error = await errorFrom(request("/api/studio/agents/planner", { method: "DELETE" }));

    expect(error.status).toBe(502);
    expect(error.message).toBe("<html>502 Bad Gateway</html>");
    expect(error.payload).toBeUndefined();
  });

  it("carries a validation body too, so a 422 is readable without a second fetch", async () => {
    // The slug rejection's `detail` is a sentence (AC9), but the shape has to
    // survive whatever the server sends.
    respondWith(
      JSON.stringify({
        detail: "slug cannot be changed after creation; duplicate the agent under a new slug instead",
      }),
      { status: 422 },
    );

    const error = await errorFrom(request("/api/studio/agents/planner", { method: "PATCH" }));

    expect(error.status).toBe(422);
    expect(error.message).toContain("slug");
    expect(error.payload).toEqual({
      detail:
        "slug cannot be changed after creation; duplicate the agent under a new slug instead",
    });
  });
});
