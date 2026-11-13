/**
 * The Docker cleanup dialog's outcome report: a read-only list of steps with
 * no control in it, so it is its own tab stop and must be reachable, give way
 * to a line when nothing ran, and never swallow the dialog's Escape.
 */

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DockerCleanupModal } from "../DockerCleanupModal";
import { diskApi } from "../../../api/diskApi";
import type {
  DockerReclaimPreview,
  DockerReclaimResult,
} from "../../../api/diskTypes";

jest.mock("../../../api/diskApi", () => ({
  diskApi: { reclaimDocker: jest.fn() },
}));
const mockReclaim = diskApi.reclaimDocker as jest.MockedFunction<
  typeof diskApi.reclaimDocker
>;

const OUTCOME = "Docker cleanup outcome by step";

const PREVIEW: DockerReclaimPreview = {
  unused_images: 2,
  unused_images_bytes: 2_000_000_000,
  build_cache_entries: 0,
  build_cache_bytes: 0,
  volumes: [],
  volumes_bytes: 0,
  kept_named_volumes: [],
  total_bytes: 2_000_000_000,
};

async function runCleanup(result: DockerReclaimResult) {
  mockReclaim.mockResolvedValue(result);
  const onClose = jest.fn();
  const onCleaned = jest.fn();
  render(
    <DockerCleanupModal
      open
      preview={PREVIEW}
      onClose={onClose}
      onCleaned={onCleaned}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: /^Free about .* from Docker$/ }),
  );
  await waitFor(() => expect(onCleaned).toHaveBeenCalled());
  expect(mockReclaim).toHaveBeenCalledWith({
    images: true,
    build_cache: false,
    volumes: [],
  });
  return { onClose };
}

const ONE_STEP: DockerReclaimResult = {
  steps: [
    {
      label: "Unused images",
      outcome: "deleted",
      reclaimed_bytes: 2_000_000_000,
      detail: "",
    },
  ],
  reclaimed_bytes: 2_000_000_000,
  failed_count: 0,
};

beforeEach(() => mockReclaim.mockReset());

describe("the Docker outcome report's keyboard path", () => {
  it("is a list, named, and a tab stop stepping on to Done — AC3", async () => {
    await runCleanup(ONE_STEP);

    const list = await screen.findByRole("list", { name: OUTCOME });
    expect(list).toHaveAttribute("tabindex", "0");
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);

    // After the cleanup the footer holds only Done, and it follows the report.
    list.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Done" })).toHaveFocus();
  });

  it("puts a line in its place when no step ran — AC8", async () => {
    await runCleanup({ steps: [], reclaimed_bytes: 0, failed_count: 0 });

    expect(await screen.findByText("Nothing ran.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: OUTCOME })).toBeNull();
  });

  it("lets Escape from inside the report still close the dialog — AC9", async () => {
    const { onClose } = await runCleanup(ONE_STEP);

    const list = await screen.findByRole("list", { name: OUTCOME });
    list.focus();
    fireEvent.keyDown(list, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
