/**
 * The viewport write's effect on the cache — the half a page test cannot see.
 *
 * `ViewPageCanvas.test.tsx` pins what the surface *sends*. What it cannot show
 * is what happens to the record react-query is holding when the response lands,
 * and that is where a viewport write can do damage it never intended: the server
 * answers with the whole view, and writing the whole thing back would revert a
 * layout edit that landed beside it, or resurrect a view the user just closed.
 *
 * Both are asserted here against the cache directly rather than through a
 * rendered surface, because the cache entry *is* the thing under test.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";

import { updateView, viewsKeys, type ViewSummary } from "../../lib/viewsApi";
import { useViewViewportWrite } from "../useViewViewportWrite";

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  updateView: jest.fn(),
}));

const mockUpdateView = updateView as jest.MockedFunction<typeof updateView>;

const SLUG = "loregarden";
const VIEW_ID = "v-canvas";
const KEY = viewsKeys.view(SLUG, VIEW_ID);

type Json = Record<string, unknown>;

const canvasLayout = (items: Json[]): Json => ({ kind: "canvas", containers: {}, items });

function view(layout: Json, viewport: Json): ViewSummary {
  return {
    id: VIEW_ID,
    kind: "canvas",
    title: "Sketch Surface",
    icon: "",
    layout,
    viewport,
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  };
}

function Harness({ client }: { client: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <Writer />
    </QueryClientProvider>
  );
}

function Writer() {
  const write = useViewViewportWrite(SLUG, VIEW_ID);
  return (
    <button type="button" onClick={() => write({ pan_x: 120, pan_y: 60, zoom: 2 })}>
      pan
    </button>
  );
}

function testClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } },
  });
}

beforeEach(() => {
  mockUpdateView.mockReset();
});

it("sends the viewport alone, under the view it was issued against", async () => {
  const client = testClient();
  client.setQueryData(KEY, view(canvasLayout([]), {}));
  mockUpdateView.mockResolvedValue(view(canvasLayout([]), { pan_x: 120, pan_y: 60, zoom: 2 }));
  render(<Harness client={client} />);

  act(() => {
    screen.getByText("pan").click();
  });

  await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
  expect(mockUpdateView.mock.calls[0]).toEqual([
    SLUG,
    VIEW_ID,
    { viewport: { pan_x: 120, pan_y: 60, zoom: 2 } },
  ]);
});

it("merges only the viewport, leaving a layout edit that landed beside it alone", async () => {
  // The server's record is authoritative about the field this write set and
  // stale about everything else: a layout PATCH may have landed between this
  // request being sent and its response arriving. Writing the whole record back
  // would revert that edit in the cache, and the *next* layout write — composed
  // from the cache — would then PATCH the reverted layout back to the server.
  const client = testClient();
  const edited = canvasLayout([{ id: "i-1" }]);
  client.setQueryData(KEY, view(edited, {}));
  mockUpdateView.mockResolvedValue(
    view(canvasLayout([]), { pan_x: 120, pan_y: 60, zoom: 2 }),
  );
  render(<Harness client={client} />);

  act(() => {
    screen.getByText("pan").click();
  });

  await waitFor(() =>
    expect(client.getQueryData<ViewSummary>(KEY)?.viewport).toEqual({
      pan_x: 120,
      pan_y: 60,
      zoom: 2,
    }),
  );
  expect(client.getQueryData<ViewSummary>(KEY)?.layout).toBe(edited);
});

it("does not put a view back that left the cache while the write was open", async () => {
  // Deleted from the sidebar, or closed in another tab, while a settled pan was
  // in flight. A viewport write is the last thing that should decide a view
  // still exists.
  const client = testClient();
  client.setQueryData(KEY, view(canvasLayout([]), {}));
  let land: (record: ViewSummary) => void = () => undefined;
  mockUpdateView.mockImplementation(
    () => new Promise<ViewSummary>((resolve) => (land = resolve)),
  );
  render(<Harness client={client} />);

  act(() => {
    screen.getByText("pan").click();
  });
  await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));

  client.removeQueries({ queryKey: KEY });
  await act(async () => {
    land(view(canvasLayout([]), { pan_x: 120, pan_y: 60, zoom: 2 }));
  });

  expect(client.getQueryData<ViewSummary>(KEY)).toBeUndefined();
});

it("does nothing outside a view route, where there is no id to PATCH", async () => {
  const client = testClient();
  function Nowhere() {
    const write = useViewViewportWrite(SLUG, "");
    return (
      <button type="button" onClick={() => write({ pan_x: 1, pan_y: 1, zoom: 1 })}>
        pan
      </button>
    );
  }
  render(
    <QueryClientProvider client={client}>
      <Nowhere />
    </QueryClientProvider>,
  );

  act(() => {
    screen.getByText("pan").click();
  });

  expect(mockUpdateView).not.toHaveBeenCalled();
});
