/**
 * The wire contract for creating and reading a single view.
 *
 * `viewsApi.ts` exists (433/434 shipped its list, patch and delete calls); the
 * three exports asserted here do not, so these failures are missing functions
 * rather than a missing module.
 *
 * Why the create body gets a test of its own: `ViewCreate` in
 * `server/loregarden/api/views.py` is `extra="forbid"`, and the view's kind is
 * *the layout's* discriminator, not a field of the request. A client that sends
 * the kind it just asked the user to pick — the obvious thing to do, since the
 * New View form collects exactly that — gets a 422 at runtime and nothing
 * anywhere in the client says so. The test is here because the mistake is one
 * keystroke away and its symptom is a modal that silently refuses to close.
 *
 * The query-key factory is here for a narrower reason: creating a view writes
 * two server-side lists in one transaction (the view, and its sidebar entry),
 * so the create path has to invalidate two caches. Those caches belong to
 * `useSidebarTabs`, which spells its keys inline today. Two spellings of one key
 * is a cache that silently stops refreshing when one of them is edited.
 */

import fs from "fs";
import path from "path";

import { ApiError } from "../../api/http";
import { assertServerAcceptableLayout } from "../../test/viewLayoutContract";
import { createView, fetchView, viewsKeys } from "../viewsApi";

const SLUG = "loregarden";

const LAYOUT = {
  kind: "flex_grid",
  containers: { "c-1": { kind: "panel", settings: {} } },
  root: { node: "leaf", id: "n-1", size: 1, container_id: "c-1" },
};

const CREATED = {
  id: "v-new",
  kind: "flex_grid",
  title: "Roadmap",
  icon: "",
  layout: LAYOUT,
  created_at: "2026-08-14T00:00:00",
  updated_at: "2026-08-14T00:00:00",
};

function mockJson(status: number, body: unknown) {
  const text = JSON.stringify(body);
  const fetchMock = jest.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
    text: async () => text,
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

/** The one request `request()` issued, as `[path, init]`. */
function soleCall(fetchMock: jest.Mock): [string, RequestInit] {
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return [url, init];
}

describe("createView — the wire contract a 422 would otherwise teach at runtime", () => {
  it("the layout this suite posts is one the server accepts", () => {
    // Otherwise "the body carried the layout through" is satisfied by carrying
    // a layout that would 400 on arrival.
    assertServerAcceptableLayout(LAYOUT);
  });

  it("POSTs the workspace's view collection", async () => {
    const fetchMock = mockJson(201, CREATED);

    await expect(
      createView(SLUG, { title: "Roadmap", icon: "", layout: LAYOUT }),
    ).resolves.toEqual(CREATED);

    const [url, init] = soleCall(fetchMock);
    expect(url).toContain(`/api/workspaces/${SLUG}/views`);
    expect(init.method).toBe("POST");
  });

  it("sends no top-level `kind` — the kind is the layout's tag", async () => {
    const fetchMock = mockJson(201, CREATED);

    await createView(SLUG, { title: "Roadmap", icon: "", layout: LAYOUT });

    const [, init] = soleCall(fetchMock);
    const body = JSON.parse(init.body as string) as Record<string, unknown>;

    expect(body).not.toHaveProperty("kind");
    // `extra="forbid"` refuses anything outside these three, so the assertion is
    // on the whole key set rather than on `kind` alone.
    for (const key of Object.keys(body)) expect(["title", "icon", "layout"]).toContain(key);
    expect(body.layout).toEqual(LAYOUT);
    // Not just "some layout went through": the tag the server discriminates on
    // has to survive serialization, and the body has to be one it would accept.
    expect((body.layout as Record<string, unknown>).kind).toBe("flex_grid");
    assertServerAcceptableLayout(body.layout);
  });

  it("does not mutate the layout it was handed", async () => {
    // The caller's layout is the seed it may post again, or the cached record a
    // duplicate copied from; a serializer that stamped an id into it in passing
    // would corrupt both, and nothing on screen would say so.
    mockJson(201, CREATED);
    const layout = JSON.parse(JSON.stringify(LAYOUT));
    await createView(SLUG, { title: "Roadmap", icon: "", layout });
    expect(layout).toEqual(LAYOUT);
  });

  it("escapes the workspace slug into the path", async () => {
    const fetchMock = mockJson(201, CREATED);
    await createView("a b/c", { title: "", icon: "", layout: LAYOUT });
    const [url] = soleCall(fetchMock);
    expect(url).toContain("/api/workspaces/a%20b%2Fc/views");
  });

  it("surfaces a refused layout as an ApiError carrying its status", async () => {
    // 400 and 422 are both "fix the request"; what the caller needs from this
    // layer is the status, so the modal can show the reason instead of retrying.
    mockJson(422, { detail: "Extra inputs are not permitted" });

    await expect(
      createView(SLUG, { title: "", icon: "", layout: LAYOUT }),
    ).rejects.toMatchObject({ status: 422 });
  });
});

describe("fetchView — the read the /view/:viewId route depends on", () => {
  it("GETs one view by id, with the id escaped", async () => {
    const fetchMock = mockJson(200, CREATED);

    await expect(fetchView(SLUG, "v new")).resolves.toEqual(CREATED);

    const [url, init] = soleCall(fetchMock);
    expect(url).toContain(`/api/workspaces/${SLUG}/views/v%20new`);
    expect(init.method ?? "GET").toBe("GET");
  });

  it("rejects a deleted view with a 404 the route can distinguish", async () => {
    // AC4 turns on this status specifically: a not-found state is right for a
    // 404 and wrong for anything else.
    mockJson(404, { detail: "View not found" });

    const error = await fetchView(SLUG, "gone").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
  });
});

describe("viewsKeys — one spelling of the two caches a create invalidates", () => {
  it("produces the keys the sidebar's own queries already use", () => {
    // These exact arrays are what `useSidebarTabs` reads under today. If the
    // factory disagrees with them, the create path invalidates nothing and the
    // new tab appears only after an unrelated refetch.
    expect(viewsKeys.views(SLUG)).toEqual(["views", SLUG]);
    expect(viewsKeys.sidebarEntries(SLUG)).toEqual(["sidebar-entries", SLUG]);
  });

  it("keys one view distinctly from the list and from another view", () => {
    // The spelling is not pinned — several are defensible — but the collisions
    // are. A `view` key equal to the list key makes `fetchView`'s result
    // overwrite the sidebar's array; a `view` key that ignores its id or its
    // slug serves one view's record for another's route.
    const one = viewsKeys.view(SLUG, "v-1");
    expect(one).toEqual(expect.arrayContaining(["v-1", SLUG]));
    expect(one).not.toEqual(viewsKeys.views(SLUG));
    expect(one).not.toEqual(viewsKeys.sidebarEntries(SLUG));
    expect(one).not.toEqual(viewsKeys.view(SLUG, "v-2"));
    expect(one).not.toEqual(viewsKeys.view("other-workspace", "v-1"));
  });

  it("is the sidebar hook's source of keys, not a second copy of them", () => {
    // A source assertion because the duplication it forbids is invisible at
    // runtime: two identical inline arrays behave correctly right up until one
    // of them is edited. Same technique as PrimitivePicker.test.tsx's
    // import-boundary check, and for the same reason.
    const source = fs
      .readFileSync(path.join(__dirname, "..", "..", "hooks", "useSidebarTabs.ts"), "utf8")
      // Comments quote key spellings when they explain them; only code counts.
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");

    // Imported, not merely mentioned.
    expect(source).toMatch(/import\s*\{[^}]*\bviewsKeys\b[^}]*\}\s*from\s*["'][^"']*viewsApi["']/);
    expect(source).not.toMatch(/\[\s*["']sidebar-entries["']\s*,/);
    expect(source).not.toMatch(/\[\s*["']views["']\s*,/);
  });
});
