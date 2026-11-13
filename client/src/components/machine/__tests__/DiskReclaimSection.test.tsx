import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { diskApi } from "../../../api/diskApi";
import type {
  DockerReclaimPreview,
  LocalModel,
  LocalModelList,
} from "../../../api/diskTypes";
import { ApiError } from "../../../api/http";
import { findUsabilityProblems } from "../../../lib/usabilityCheck";
import { DiskReclaimSection } from "../DiskReclaimSection";

jest.mock("../../../api/diskApi");
const mockDisk = diskApi as jest.Mocked<typeof diskApi>;

const GB = 1024 ** 3;
const ANON = (c: string) => c.repeat(64);

/** The shape measured on this machine (2026-11-13) before its cleanup. */
function dockerPreview(
  overrides: Partial<DockerReclaimPreview> = {},
): DockerReclaimPreview {
  return {
    unused_images: 9,
    unused_images_bytes: 3.8 * GB,
    build_cache_entries: 74,
    build_cache_bytes: 3.3 * GB,
    volumes: [
      { name: ANON("a"), size_bytes: 12 * GB },
      { name: ANON("b"), size_bytes: 6.6 * GB },
    ],
    volumes_bytes: 18.6 * GB,
    kept_named_volumes: [{ name: "old_stack_db", size_bytes: 0.3 * GB }],
    total_bytes: 25.7 * GB,
    ...overrides,
  };
}

function model(overrides: Partial<LocalModel> = {}): LocalModel {
  return {
    id: "lm_studio:lmstudio-community/Qwen3.6-35B-A3B-GGUF",
    source: "lm_studio",
    name: "lmstudio-community/Qwen3.6-35B-A3B-GGUF",
    path: "/Users/me/.lmstudio/models/lmstudio-community/Qwen3.6-35B-A3B-GGUF",
    size_bytes: 20.5 * GB,
    frees_bytes: 20.5 * GB,
    modified_at: "2026-05-08T12:00:00",
    links_to: null,
    broken: false,
    linked_by: [],
    ...overrides,
  };
}

/** Before the cleanup: four LM Studio models, Ollama models, one LM Studio link. */
function liveModels(): LocalModelList {
  return {
    locations: [
      {
        source: "lm_studio",
        path: "/Users/me/.lmstudio/models",
        found: true,
        error: "",
      },
      {
        source: "ollama",
        path: "/Users/me/.ollama/models",
        found: true,
        error: "",
      },
    ],
    models: [
      model(),
      model({
        id: "lm_studio:lmstudio-community/Qwen3.5-35B-A3B-GGUF",
        name: "lmstudio-community/Qwen3.5-35B-A3B-GGUF",
      }),
      model({
        id: "ollama:registry.ollama.ai/library/llama3.1/8b",
        source: "ollama",
        name: "llama3.1:8b",
        path: "/Users/me/.ollama/models/manifests/registry.ollama.ai/library/llama3.1/8b",
        size_bytes: 4.9 * GB,
        frees_bytes: 4.9 * GB,
        linked_by: ["unknown/llama3.1-8b-GGUF"],
      }),
      model({
        id: "lm_studio:unknown/llama3.1-8b-GGUF",
        name: "unknown/llama3.1-8b-GGUF",
        size_bytes: 111,
        frees_bytes: 111,
        links_to: "ollama",
      }),
    ],
  };
}

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DiskReclaimSection />
    </QueryClientProvider>,
  );
}

function card(name: RegExp) {
  return screen.getByRole("article", { name });
}

beforeEach(() => {
  jest.resetAllMocks();
  mockDisk.dockerReclaimable.mockResolvedValue(dockerPreview());
  mockDisk.models.mockResolvedValue(liveModels());
});

describe("Docker card", () => {
  it("shows what Docker could free and opens the review", async () => {
    const user = userEvent.setup();
    renderSection();
    const docker = card(/docker/i);
    expect(await within(docker).findByText(/About 25\.7 GB unused/)).toBeTruthy();

    const open = within(docker).getByRole("button", {
      name: "Review Docker cleanup…",
    });
    expect(open.getAttribute("aria-expanded")).toBe("false");
    await user.click(open);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Kept: 1 named volume/)).toBeTruthy();
    expect(
      within(dialog).getByRole("button", {
        name: "Free about 25.7 GB from Docker",
      }),
    ).toBeTruthy();
  });

  it("sends only the parts left ticked, and keeps the result on screen after the refetch", async () => {
    const user = userEvent.setup();
    mockDisk.reclaimDocker.mockResolvedValue({
      steps: [
        {
          label: "2 of 2 unattached volumes",
          outcome: "deleted",
          reclaimed_bytes: 18.6 * GB,
          detail: "",
        },
        {
          label: "Unused images",
          outcome: "deleted",
          reclaimed_bytes: 3.8 * GB,
          detail: "",
        },
      ],
      reclaimed_bytes: 22.4 * GB,
      failed_count: 0,
    });
    renderSection();
    await user.click(
      await screen.findByRole("button", { name: "Review Docker cleanup…" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("checkbox", { name: /Build cache/ }));

    // After the cleanup, Docker has nothing left to free.
    mockDisk.dockerReclaimable.mockResolvedValue(
      dockerPreview({
        unused_images: 0,
        unused_images_bytes: 0,
        build_cache_entries: 0,
        build_cache_bytes: 0,
        volumes: [],
        volumes_bytes: 0,
        total_bytes: 0,
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: /^Free about/ }),
    );

    expect(mockDisk.reclaimDocker).toHaveBeenCalledWith({
      images: true,
      build_cache: false,
      volumes: [ANON("a"), ANON("b")],
    });
    expect(
      await within(dialog).findByText(/Docker freed 22\.4 GB/),
    ).toBeTruthy();
    await waitFor(() =>
      expect(within(card(/docker/i)).getByText(/Nothing to free/)).toBeTruthy(),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("keeps the dialog open with the reason when the cleanup fails", async () => {
    const user = userEvent.setup();
    mockDisk.reclaimDocker.mockRejectedValue(
      new ApiError(503, "Docker is not answering: daemon gone"),
    );
    renderSection();
    await user.click(
      await screen.findByRole("button", { name: "Review Docker cleanup…" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^Free about/ }));
    expect((await within(dialog).findByRole("alert")).textContent).toMatch(
      /daemon gone/,
    );
    expect(
      within(dialog).getByRole("button", { name: /^Free about/ }),
    ).toBeTruthy();
  });

  it("says calmly when Docker is not running, with no alert", async () => {
    mockDisk.dockerReclaimable.mockRejectedValue(
      new ApiError(503, "Docker is not installed (no `docker` command found)."),
    );
    renderSection();
    const docker = card(/docker/i);
    expect(
      await within(docker).findByText(/Nothing to check until it runs/),
    ).toBeTruthy();
    expect(within(docker).queryByRole("alert")).toBeNull();
    expect(within(docker).getByRole("button", { name: "Check again" })).toBeTruthy();
  });

  it("shows any other failure as an error with a retry", async () => {
    mockDisk.dockerReclaimable.mockRejectedValue(
      new ApiError(502, "`docker system df` output was not understood"),
    );
    renderSection();
    const alert = await within(card(/docker/i)).findByRole("alert");
    expect(alert.textContent).toMatch(/not understood/);
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});

describe("Downloaded models card", () => {
  it("lists every model largest first, each with a named delete button", async () => {
    renderSection();
    const models = card(/downloaded models/i);
    const table = await within(models).findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(4);
    expect(within(rows[0]).getByRole("rowheader").textContent).toMatch(
      /Qwen3\.6-35B/,
    );
    expect(within(rows[3]).getByText("Link to Ollama")).toBeTruthy();
    expect(
      within(table).getByRole("button", { name: "Delete llama3.1:8b" }),
    ).toBeTruthy();
    expect(findUsabilityProblems(models)).toEqual([]);
  });

  it("deletes one after confirmation, naming the LM Studio links that go too", async () => {
    const user = userEvent.setup();
    mockDisk.deleteModel.mockResolvedValue({
      id: "ollama:registry.ollama.ai/library/llama3.1/8b",
      name: "llama3.1:8b",
      outcome: "deleted",
      freed_bytes: 4.9 * GB,
      detail: "Also removed the LM Studio link(s) to it: unknown/llama3.1-8b-GGUF.",
    });
    renderSection();
    await user.click(
      await screen.findByRole("button", { name: "Delete llama3.1:8b" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/these go too: unknown\/llama3\.1-8b-GGUF/)).toBeTruthy();
    await user.click(
      within(dialog).getByRole("button", { name: "Delete and free 4.9 GB" }),
    );
    expect(mockDisk.deleteModel).toHaveBeenCalledWith(
      "ollama:registry.ollama.ai/library/llama3.1/8b",
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(mockDisk.models).toHaveBeenCalledTimes(2);
  });

  it("stays open with the server's reason when the delete fails", async () => {
    const user = userEvent.setup();
    mockDisk.deleteModel.mockResolvedValue({
      id: model().id,
      name: model().name,
      outcome: "failed",
      freed_bytes: 0,
      detail: "Could not delete it: [Errno 1] Operation not permitted.",
    });
    renderSection();
    await user.click(
      await screen.findByRole("button", { name: `Delete ${model().name}` }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^Delete and free/ }));
    expect((await within(dialog).findByRole("alert")).textContent).toMatch(
      /Operation not permitted/,
    );
  });

  it("calls a link a link, and says deleting it frees almost nothing", async () => {
    const user = userEvent.setup();
    renderSection();
    await user.click(
      await screen.findByRole("button", {
        name: "Delete unknown/llama3.1-8b-GGUF",
      }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/only a link to files Ollama keeps/)).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Delete link" })).toBeTruthy();
  });

  it("tells a broken link apart from a working one", async () => {
    const user = userEvent.setup();
    const list = liveModels();
    list.models[3] = { ...list.models[3], broken: true };
    mockDisk.models.mockResolvedValue(list);
    renderSection();
    expect(await screen.findByText("Broken link")).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "Delete unknown/llama3.1-8b-GGUF" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/already gone/)).toBeTruthy();
  });

  it("says where it looked when there are no models", async () => {
    mockDisk.models.mockResolvedValue({ ...liveModels(), models: [] });
    renderSection();
    expect(
      await within(card(/downloaded models/i)).findByText(
        /No LM Studio or Ollama models found\. Looked in \/Users\/me\/\.lmstudio\/models and \/Users\/me\/\.ollama\/models/,
      ),
    ).toBeTruthy();
  });

  it("shows a listing failure as an error with a retry", async () => {
    mockDisk.models.mockRejectedValue(new ApiError(500, "boom"));
    renderSection();
    const alert = await within(card(/downloaded models/i)).findByRole("alert");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});
