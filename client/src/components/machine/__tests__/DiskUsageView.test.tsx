import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { diskApi } from "../../../api/diskApi";
import type {
  DiskCleanupPlan,
  DiskNode,
  DiskOverview,
} from "../../../api/diskTypes";
import { ApiError } from "../../../api/http";
import { findUsabilityProblems } from "../../../lib/usabilityCheck";
import { DiskUsageView } from "../DiskUsageView";

jest.mock("../../../api/diskApi");
const mockDisk = diskApi as jest.Mocked<typeof diskApi>;

const GB = 1024 ** 3;
const TREES = "/Users/me/workspace/.worktrees";

function dir(
  path: string,
  size: number,
  kind: DiskNode["kind"],
  children: DiskNode[] = [],
): DiskNode {
  return {
    name: path.split("/").pop()!,
    path,
    size_bytes: size,
    kind,
    is_dir: true,
    file_count: 100,
    children,
    folded_count: 0,
  };
}

/**
 * The shape measured on the live machine (2026-10-23): one worktree directory
 * of 50 ticket checkouts, 25 GB, each 0.4 to 1.7 GB, mostly node_modules and
 * build output; and the loregarden checkout at ~10 GB.
 */
function liveShapeRoots(): DiskNode[] {
  const trees = Array.from({ length: 50 }, (_, index) => {
    const path = `${TREES}/ticket-loregarden-${index}-${(index * 7919).toString(16)}`;
    const size = Math.round((1.7 - index * 0.026) * GB);
    return dir(path, size, "worktree", [
      dir(`${path}/node_modules`, Math.round(size * 0.6), "dependencies"),
      dir(`${path}/target`, Math.round(size * 0.3), "build_output"),
      dir(
        `${path}/src`,
        size - Math.round(size * 0.6) - Math.round(size * 0.3),
        "directory",
      ),
    ]);
  });
  return [
    dir(
      TREES,
      trees.reduce((sum, t) => sum + t.size_bytes, 0),
      "directory",
      trees,
    ),
    dir("/Users/me/workspace/loregarden", 10 * GB, "workspace", [
      dir("/Users/me/workspace/loregarden/data", 4 * GB, "loregarden_data"),
      dir("/Users/me/workspace/loregarden/.claude", 5 * GB, "directory"),
      dir("/Users/me/workspace/loregarden/client", 1 * GB, "directory"),
    ]),
  ];
}

function overview(overrides: Partial<DiskOverview> = {}): DiskOverview {
  const roots = liveShapeRoots();
  return {
    volume: {
      path: "/",
      total_bytes: 926 * GB,
      used_bytes: 917 * GB,
      free_bytes: 9 * GB,
    },
    status: {
      state: "done",
      started_at: null,
      entries: 999187,
      current_root: "",
      error: "",
    },
    scan: {
      started_at: "2026-10-23T10:00:00",
      finished_at: "2026-10-23T10:01:00",
      entries: 999187,
      unreadable: 0,
      unreadable_samples: [],
      truncated: false,
      unreadable_workspaces: [],
      missing_added: [],
      total_bytes: roots.reduce((sum, r) => sum + r.size_bytes, 0),
      roots: [
        {
          path: TREES,
          label: "loregarden worktrees",
          origin: "worktrees",
          workspace_slug: "loregarden",
        },
        {
          path: "/Users/me/workspace/loregarden",
          label: "loregarden",
          origin: "workspace",
          workspace_slug: "loregarden",
        },
      ],
    },
    added_roots: [],
    ...overrides,
  };
}

function renderView(path = "/machine/disk") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <DiskUsageView />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => 800,
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get: () => 480,
  });
});

beforeEach(() => {
  jest.resetAllMocks();
  mockDisk.history.mockResolvedValue([]);
  mockDisk.tree.mockImplementation(async (path: string) => {
    const roots = liveShapeRoots();
    if (!path) return roots;
    const found = roots
      .flatMap((r) => [r, ...r.children])
      .find((n) => n.path === path);
    if (!found)
      throw new ApiError(404, `The last scan has no detail for ${path}.`);
    return [found];
  });
});

describe("DiskUsageView", () => {
  it("before any scan, says what a scan does and offers one", async () => {
    mockDisk.overview.mockResolvedValue(overview({ scan: null }));
    mockDisk.scan.mockResolvedValue({
      state: "running",
      started_at: null,
      entries: 0,
      current_root: "",
      error: "",
    });
    renderView();

    expect(
      await screen.findByRole("heading", { name: "Where did the disk go?" }),
    ).toBeTruthy();
    expect(screen.getByText(/never opens a file/)).toBeTruthy();
    await userEvent.click(
      screen.getAllByRole("button", { name: "Scan now" })[0],
    );
    expect(mockDisk.scan).toHaveBeenCalledTimes(1);
    expect(mockDisk.tree).not.toHaveBeenCalled();
  });

  it("while scanning, shows the live entry count and disables a second scan", async () => {
    mockDisk.overview.mockResolvedValue(
      overview({
        status: {
          state: "running",
          started_at: null,
          entries: 412000,
          current_root: TREES,
          error: "",
        },
      }),
    );
    renderView();

    expect(await screen.findByText(/Scanning… 412,000 entries/)).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Scanning…" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("a failed overview read is an error with a retry, not an empty page", async () => {
    mockDisk.overview.mockRejectedValue(
      new ApiError(500, "The stored disk scan could not be read"),
    );
    renderView();

    expect((await screen.findByRole("alert")).textContent).toMatch(
      /could not be read/,
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("at live volume, draws every root as a tile and every entry as a row, and is usable", async () => {
    mockDisk.overview.mockResolvedValue(overview());
    const { container } = renderView();

    const map = await screen.findByRole("group", {
      name: /Disk use inside All scanned folders/,
    });
    expect(
      within(map).getByRole("button", {
        name: /^\.worktrees, Folder, [\d.]+ GB\. Open$/,
      }),
    ).toBeTruthy();
    expect(within(map).getAllByRole("button").length).toBeGreaterThan(40);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(findUsabilityProblems(container)).toEqual([]);
  });

  it("drilling into a folder follows the URL and the find box narrows the table", async () => {
    mockDisk.overview.mockResolvedValue(overview());
    renderView();

    await userEvent.click(
      await screen.findByRole("button", { name: ".worktrees" }),
    );

    await waitFor(() => expect(mockDisk.tree).toHaveBeenCalledWith(TREES, 2));
    expect(
      await screen.findByRole("searchbox", { name: /Find by name in/ }),
    ).toBeTruthy();
    expect(
      within(screen.getByRole("navigation", { name: "Folder path" })).getByText(
        "loregarden worktrees",
      ),
    ).toBeTruthy();
    await userEvent.type(screen.getByRole("searchbox"), "ticket-loregarden-1-");
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(
      2,
    );
  });

  it("never offers a checkbox on loregarden's own data or a scanned root", async () => {
    mockDisk.overview.mockResolvedValue(overview());
    renderView("/machine/disk?path=/Users/me/workspace/loregarden");

    await screen.findByRole("table");
    expect(
      screen.queryByRole("checkbox", { name: "Mark data for cleanup" }),
    ).toBeNull();
    expect(
      screen.getByRole("checkbox", { name: "Mark client for cleanup" }),
    ).toBeTruthy();
  });

  it("marks, reviews on the server, and moves only what the person confirmed", async () => {
    const keep = `${TREES}/ticket-loregarden-0-0`;
    const go = `${TREES}/ticket-loregarden-1-1eef`;
    mockDisk.overview.mockResolvedValue(overview());
    const plan: DiskCleanupPlan = {
      plan_id: "plan-1",
      expires_at: "2026-10-23T10:10:00",
      allowed_bytes: 1.67 * GB,
      allowed_count: 1,
      refused_count: 1,
      covered: [],
      items: [
        {
          size_bytes: 1.67 * GB,
          verdict: {
            path: go,
            kind: "worktree",
            allowed: true,
            method: "trash_worktree",
            reasons: [],
            notes: [
              "Branch feature keeps every commit. Only the checkout goes.",
            ],
            fingerprint: "1:2:3",
            worktree_repo: "/Users/me/workspace/loregarden",
          },
        },
        {
          size_bytes: 1.7 * GB,
          verdict: {
            path: keep,
            kind: "worktree",
            allowed: false,
            method: null,
            reasons: [
              "It has uncommitted or untracked changes that exist nowhere else.",
            ],
            notes: [],
            fingerprint: "",
            worktree_repo: "",
          },
        },
      ],
    };
    mockDisk.previewCleanup.mockResolvedValue(plan);
    mockDisk.executeCleanup.mockResolvedValue({
      results: [
        { path: go, outcome: "trashed", size_bytes: 1.67 * GB, detail: "" },
      ],
      trashed_bytes: 1.67 * GB,
      trashed_count: 1,
      refused_count: 0,
      failed_count: 0,
    });
    renderView(`/machine/disk?path=${TREES}`);

    await userEvent.click(
      await screen.findByRole("checkbox", {
        name: "Mark ticket-loregarden-0-0 for cleanup",
      }),
    );
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: "Mark ticket-loregarden-1-1eef for cleanup",
      }),
    );
    expect(
      screen.getByRole("region", { name: "Marked for cleanup" }).textContent,
    ).toMatch(/2 marked/);
    await userEvent.click(
      screen.getByRole("button", { name: "Review cleanup…" }),
    );

    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(/uncommitted or untracked changes/),
    ).toBeTruthy();
    expect(within(dialog).getByText(/keeps every commit/)).toBeTruthy();
    expect(mockDisk.previewCleanup).toHaveBeenCalledWith(
      expect.arrayContaining([keep, go]),
    );

    await userEvent.click(
      within(dialog).getByRole("button", {
        name: /^Move 1 item \(1\.7 GB\) to Trash$/,
      }),
    );

    expect(mockDisk.executeCleanup).toHaveBeenCalledWith("plan-1", [go]);
    expect(
      await within(dialog).findByText(/Empty the Trash in Finder/),
    ).toBeTruthy();
  });

  it("unticking every allowed item leaves nothing to confirm", async () => {
    const go = `${TREES}/ticket-loregarden-1-1eef`;
    mockDisk.overview.mockResolvedValue(overview());
    mockDisk.previewCleanup.mockResolvedValue({
      plan_id: "p",
      expires_at: "",
      allowed_bytes: GB,
      allowed_count: 1,
      refused_count: 0,
      covered: [],
      items: [
        {
          size_bytes: GB,
          verdict: {
            path: go,
            kind: "worktree",
            allowed: true,
            method: "trash",
            reasons: [],
            notes: [],
            fingerprint: "f",
            worktree_repo: "",
          },
        },
      ],
    });
    renderView(`/machine/disk?path=${TREES}`);

    await userEvent.click(
      await screen.findByRole("checkbox", {
        name: "Mark ticket-loregarden-1-1eef for cleanup",
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Review cleanup…" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("checkbox"));

    const confirm = within(dialog).getByRole("button", {
      name: /^Move 0 items/,
    }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(mockDisk.executeCleanup).not.toHaveBeenCalled();
  });

  it("an expired preview is said, and checking again asks the server again", async () => {
    const go = `${TREES}/ticket-loregarden-1-1eef`;
    mockDisk.overview.mockResolvedValue(overview());
    mockDisk.previewCleanup.mockResolvedValue({
      plan_id: "p",
      expires_at: "",
      allowed_bytes: GB,
      allowed_count: 1,
      refused_count: 0,
      covered: [],
      items: [
        {
          size_bytes: GB,
          verdict: {
            path: go,
            kind: "worktree",
            allowed: true,
            method: "trash",
            reasons: [],
            notes: [],
            fingerprint: "f",
            worktree_repo: "",
          },
        },
      ],
    });
    mockDisk.executeCleanup.mockRejectedValue(
      new ApiError(
        409,
        "This cleanup preview has expired or was already used.",
      ),
    );
    renderView(`/machine/disk?path=${TREES}`);

    await userEvent.click(
      await screen.findByRole("checkbox", {
        name: "Mark ticket-loregarden-1-1eef for cleanup",
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Review cleanup…" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      await within(dialog).findByRole("button", { name: /^Move 1 item/ }),
    );

    expect((await within(dialog).findByRole("alert")).textContent).toMatch(
      /expired/,
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Check again" }),
    );
    await waitFor(() =>
      expect(mockDisk.previewCleanup).toHaveBeenCalledTimes(2),
    );
  });
});
