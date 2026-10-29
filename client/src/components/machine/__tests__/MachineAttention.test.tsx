import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { MemoryRouter } from "react-router-dom";

import type { DiskVolume } from "../../../api/diskTypes";
import { machineAttention } from "../../../lib/machineAttention";
import { capacityStatus, lease } from "../../../test/dockerFixtures";
import { MachineAttention } from "../MachineAttention";

const GB = 1024 ** 3;
const fullDisk: DiskVolume = {
  path: "/",
  total_bytes: 926 * GB,
  used_bytes: 890.3 * GB,
  free_bytes: 35.7 * GB,
};

function renderBand(overrides: Partial<ComponentProps<typeof MachineAttention>> = {}) {
  const props: ComponentProps<typeof MachineAttention> = {
    items: [],
    checking: false,
    disk: { scanned: false, scanning: false, onScan: jest.fn() },
    diskError: "",
    onRetryDisk: jest.fn(),
    onEndLease: jest.fn(),
    pendingLeaseId: null,
    ...overrides,
  };
  render(
    <MemoryRouter>
      <MachineAttention {...props} />
    </MemoryRouter>,
  );
  return props;
}

describe("MachineAttention", () => {
  it("offers a scan for a full disk that was never scanned", async () => {
    const props = renderBand({ items: machineAttention(null, fullDisk) });
    const item = screen.getByTestId("attention-disk");
    expect(item).toHaveTextContent("96%");
    expect(item).toHaveTextContent("35.7 GB");
    await userEvent.click(within(item).getByRole("button", { name: "Scan now" }));
    expect(props.disk.onScan).toHaveBeenCalledTimes(1);
  });

  it("holds the scan button while a scan runs, so it cannot start twice", () => {
    renderBand({
      items: machineAttention(null, fullDisk),
      disk: { scanned: false, scanning: true, onScan: jest.fn() },
    });
    expect(screen.getByRole("button", { name: "Scanning…" })).toBeDisabled();
  });

  it("links to the Disk tab once a scan exists", () => {
    renderBand({
      items: machineAttention(null, fullDisk),
      disk: { scanned: true, scanning: false, onScan: jest.fn() },
    });
    expect(screen.getByRole("link", { name: "Where did the disk go?" })).toHaveAttribute(
      "href",
      "/machine/disk",
    );
  });

  it("ends a long-held lease through the caller's confirm, and links its run", async () => {
    const held = lease({
      held_seconds: 53.8 * 3600,
      ticket_id: "t-1",
      agent_run_id: "r-1",
      holder: { what: "POC checks", branch: "risk-downside-measures", worktree: null, pid: 32624 },
    });
    const props = renderBand({ items: machineAttention(capacityStatus({ holders: [held] }), null) });
    const item = screen.getByTestId("attention-held-lease-1");
    expect(item).toHaveTextContent("53.8 hours");
    expect(item).toHaveTextContent("pid 32624");
    expect(within(item).getByRole("link", { name: "Open run" })).toBeInTheDocument();
    await userEvent.click(within(item).getByRole("button", { name: "Release…: POC checks" }));
    expect(props.onEndLease).toHaveBeenCalledWith(held);
  });

  it("holds a lease's release while it is in flight", () => {
    const held = lease({ held_seconds: 13 * 3600 });
    renderBand({
      items: machineAttention(capacityStatus({ holders: [held] }), null),
      pendingLeaseId: "lease-1",
    });
    expect(screen.getByRole("button", { name: /^Release…/ })).toBeDisabled();
  });

  it("does not claim all is well before either read lands", () => {
    renderBand({ checking: true });
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing needs you/)).not.toBeInTheDocument();
  });

  it("says so when nothing needs a person", () => {
    renderBand();
    expect(screen.getByText(/Nothing needs you/)).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("keeps a failed disk read apart from a disk with room", async () => {
    const props = renderBand({ diskError: "disk overview: 500" });
    expect(screen.getByRole("alert")).toHaveTextContent("disk overview: 500");
    expect(screen.queryByText(/Nothing needs you/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(props.onRetryDisk).toHaveBeenCalledTimes(1);
  });
});
