/**
 * The canvas while its writes are slow: what the user sees has to follow the
 * gesture, not the write queue.
 *
 * Writes to one view run one at a time, and on a busy database a PATCH takes
 * seconds. With containers stacked, that showed up as a drag that did nothing.
 * The grabbed container moved, but underneath the ones covering it, because the
 * raise its press asked for was waiting behind every write queued ahead of it.
 * And a move still in flight when the next container was pressed never handed its
 * item back to the record, because react-query keeps only the newest `mutate`
 * call's per-call callbacks.
 *
 * A drag is one write: the press draws its container at the front and the
 * release writes the raise and the move together. Edits made while a write is
 * open go out together as one PATCH when it settles.
 *
 * Every PATCH here is held open until the test releases it, which is the slow
 * database, made deterministic.
 */

import { screen, waitFor } from "@testing-library/react";

import { ApiError } from "../../api/http";
import type { ViewSummary } from "../../lib/viewsApi";
import {
  canvasItemEl,
  drag,
  dragHandle,
  drawnBox,
  installCanvasHarness,
  mockUpdateView,
  pointerDown,
  renderCanvas,
  settle,
  storePatch,
  threeItemCanvas,
  type Json,
} from "../../test/canvasHarness";

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchView: jest.fn(),
  updateView: jest.fn(),
}));

installCanvasHarness();

interface HeldWrite {
  patch: unknown;
  succeed: () => void;
  fail: (error: Error) => void;
}

/** Every PATCH from here on waits, in order, until the test releases it. */
function holdWrites(): HeldWrite[] {
  const held: HeldWrite[] = [];
  mockUpdateView.mockImplementation(
    (_slug, _viewId, patch) =>
      new Promise<ViewSummary>((resolve, reject) => {
        held.push({
          patch,
          succeed: () => resolve(storePatch(patch)),
          fail: reject,
        });
      }),
  );
  return held;
}

async function shown(layout: Json) {
  const rendered = renderCanvas(layout);
  await screen.findByTestId("view-host");
  return rendered;
}

function topmost(root: HTMLElement): string {
  const ids = ["i-1", "i-2", "i-3"];
  return ids.reduce((top, id) => (drawnBox(root, id).zIndex > drawnBox(root, top).zIndex ? id : top));
}

function layoutOf(write: HeldWrite): Json {
  return (write.patch as { layout: Json }).layout;
}

function storedZ(layout: Json, itemId: string): number {
  const item = (layout.items as Json[]).find((each) => each.id === itemId);
  if (item === undefined) throw new Error(`No stored item ${itemId}`);
  return Number(item.z_index);
}

describe("a press raises a container before any write lands", () => {
  it("draws a grabbed container at the front at once, and writes nothing until the drag ends", async () => {
    const { container } = await shown(threeItemCanvas());
    const held = holdWrites();

    pointerDown(dragHandle(container, "i-1"), 10, 10);
    await settle();

    expect(topmost(container)).toBe("i-1");
    expect(held).toHaveLength(0);
  });

  it("writes a drag as one PATCH that both raises and moves the container", async () => {
    // A raise on the press and a move on the release used to be two writes per
    // drag, each waiting its turn behind every slow one ahead of it.
    const { container } = await shown(threeItemCanvas());
    const held = holdWrites();

    drag(dragHandle(container, "i-1"), [300, 300], [420, 360]);
    await waitFor(() => expect(held).toHaveLength(1));
    held[0].succeed();
    await settle();

    expect(held).toHaveLength(1);
    const layout = layoutOf(held[0]);
    expect(storedZ(layout, "i-1")).toBeGreaterThan(storedZ(layout, "i-3"));
    expect(drawnBox(container, "i-1")).toMatchObject({ x: 120, y: 60 });
    expect(topmost(container)).toBe("i-1");
  });

  it("keeps a grab drawn while a slow write ahead of it is still open", async () => {
    // The case the user met: a move of one container is stuck in a slow PATCH,
    // and the next container pressed has to come up now, not when that lands.
    const { container } = await shown(threeItemCanvas());
    const held = holdWrites();

    drag(dragHandle(container, "i-3"), [300, 300], [340, 320]);
    await waitFor(() => expect(held).toHaveLength(1));
    pointerDown(dragHandle(container, "i-1"), 10, 10);
    await settle();

    expect(held).toHaveLength(1);
    expect(topmost(container)).toBe("i-1");
  });

  it("returns to the record's stacking when a raise is refused", async () => {
    // A click inside a container's content is not a gesture, so its raise is a
    // write of its own.
    const { container } = await shown(threeItemCanvas());
    const held = holdWrites();

    pointerDown(canvasItemEl(container, "i-1"), 10, 10);
    await waitFor(() => expect(held).toHaveLength(1));
    expect(topmost(container)).toBe("i-1");

    held[0].fail(new ApiError(400, "Layout too large"));
    await settle();

    expect(topmost(container)).toBe("i-3");
  });
});

describe("edits behind a slow write go out together", () => {
  it("sends every edit made while a write was open as one PATCH, in order", async () => {
    const { container } = await shown(threeItemCanvas());
    const held = holdWrites();

    drag(dragHandle(container, "i-3"), [300, 300], [340, 320]);
    await waitFor(() => expect(held).toHaveLength(1));
    // Two clicks into content while the move is stuck: two raises.
    pointerDown(canvasItemEl(container, "i-1"), 10, 10);
    pointerDown(canvasItemEl(container, "i-2"), 10, 10);
    held[0].succeed();
    await waitFor(() => expect(held).toHaveLength(2));
    held[1].succeed();
    await settle();

    expect(held).toHaveLength(2);
    const layout = layoutOf(held[1]);
    expect(storedZ(layout, "i-2")).toBeGreaterThan(storedZ(layout, "i-1"));
    expect(storedZ(layout, "i-1")).toBeGreaterThan(storedZ(layout, "i-3"));
    expect(topmost(container)).toBe("i-2");
  });
});

describe("a move hands its item back to the record however later writes interleave", () => {
  it("draws the item from the record once its refused PATCH settles, even after another container was pressed", async () => {
    // Before the fix the move's settle callback was replaced by the press's
    // `mutate` call and never ran: the item stayed where it was dropped while the
    // record kept the old position, with no error on screen to say so.
    const { container } = await shown(threeItemCanvas());
    const held = holdWrites();

    drag(dragHandle(container, "i-3"), [300, 300], [420, 360]);
    await waitFor(() => expect(held).toHaveLength(1));
    expect(drawnBox(container, "i-3")).toMatchObject({ x: 240, y: 180 });

    pointerDown(canvasItemEl(container, "i-1"), 10, 10);
    held[0].fail(new ApiError(400, "Layout too large"));
    await waitFor(() => expect(held).toHaveLength(2));
    held[1].succeed();
    await settle();

    expect(drawnBox(container, "i-3")).toMatchObject({ x: 120, y: 120 });
  });
});
