import type { DiskVolume } from "../../api/diskTypes";
import { capacityStatus, lease } from "../../test/dockerFixtures";
import { LONG_HELD_SECONDS, machineAttention } from "../machineAttention";

const GB = 1024 ** 3;

function volume(usedGb: number, totalGb = 100): DiskVolume {
  return {
    path: "/",
    total_bytes: totalGb * GB,
    used_bytes: usedGb * GB,
    free_bytes: (totalGb - usedGb) * GB,
  };
}

const kinds = (items: ReturnType<typeof machineAttention>) => items.map((item) => item.kind);

describe("machineAttention", () => {
  it("is empty on a healthy machine", () => {
    expect(machineAttention(capacityStatus({ holders: [lease()] }), volume(50))).toEqual([]);
  });

  it("flags the disk past 90% full, and not at it", () => {
    expect(kinds(machineAttention(null, volume(90)))).toEqual([]);
    expect(kinds(machineAttention(null, volume(96)))).toEqual(["disk"]);
  });

  it("flags a lease held past 12 hours, and not one just under", () => {
    const under = lease({ lease_id: "a", held_seconds: LONG_HELD_SECONDS });
    const over = lease({ lease_id: "b", held_seconds: 53.8 * 3600 });
    const items = machineAttention(capacityStatus({ holders: [under, over] }), null);
    expect(items.map((item) => item.id)).toEqual(["held-b"]);
  });

  it("names an orphaned lease once, as orphaned, however long it has been held", () => {
    const orphan = lease({ status: "orphaned", held_seconds: 20 * 3600 });
    expect(kinds(machineAttention(capacityStatus({ holders: [orphan] }), null))).toEqual([
      "orphaned",
    ]);
  });

  it("lists a lease the reaper could not verify", () => {
    const status = capacityStatus({
      unverifiable: [{ lease_id: "abcdef1234", outcome: "probe_failed", error: "daemon down" }],
    });
    expect(kinds(machineAttention(status, null))).toEqual(["unverified"]);
  });

  it("says when a ceiling is stale, unmeasured or configured", () => {
    const base = capacityStatus();
    const status = capacityStatus({
      ceiling: { ...base.ceiling, source: "stale_probe", error: "Cannot connect to the Docker daemon" },
      host: { ...base.host, ceiling: { ...base.host.ceiling, source: "unknown" } },
    });
    const items = machineAttention(status, null);
    expect(items.map((item) => (item.kind === "ceiling" ? [item.id, item.tone] : null))).toEqual([
      ["ceiling-host", "bad"],
      ["ceiling-docker", "warn"],
    ]);
    const configured = capacityStatus({ ceiling: { ...base.ceiling, source: "config_override" } });
    expect(machineAttention(configured, null)).toEqual([
      expect.objectContaining({ kind: "ceiling", tone: "info" }),
    ]);
  });

  it("puts the worst first", () => {
    const status = capacityStatus({
      holders: [lease({ held_seconds: 13 * 3600 })],
      ceiling: { ...capacityStatus().ceiling, source: "config_override" },
    });
    expect(kinds(machineAttention(status, volume(96)))).toEqual(["disk", "long_held", "ceiling"]);
  });

  it("says nothing about leases while the ledger is switched off", () => {
    const off = capacityStatus({ enabled: false, holders: [lease({ held_seconds: 99 * 3600 })] });
    expect(machineAttention(off, null)).toEqual([]);
  });
});
