import { useNavigate } from "react-router-dom";

import { useDiskOverview } from "../../hooks/useDiskUsage";
import { useDockerCapacity } from "../../hooks/useDockerCapacity";
import { machinePath } from "../../lib/appNavigation";
import { formatDiskBytes, volumeTone } from "../../lib/diskUsage";
import { machineAttention } from "../../lib/machineAttention";
import { describeError } from "../../state/toastStore";
import { DockerQueueBoard } from "../DockerQueueBoard";
import { CapacityMeter } from "../ui/CapacityMeter";
import { MachineAttention } from "./MachineAttention";
import "../QueueDashboard.css";

/**
 * Machine → Capacity: what needs a person on this machine, how full it is,
 * and who holds and waits for its CPU and memory.
 *
 * One `useDockerCapacity` read feeds the band and the board, so the two cannot
 * disagree about the same moment; it polls only while this view is mounted.
 * The disk's overview is the Disk tab's own query, so a scan started here is
 * the one that tab shows in progress.
 *
 * The capacity summary is drawn once, as the board's meters. A rail beside the
 * board used to draw it a second time (free cpus, slots held, waiting) and kept
 * what needed a person behind an Attention tab nobody opened.
 */
export function MachineCapacityView() {
  const docker = useDockerCapacity();
  const { overview, scan } = useDiskOverview();
  const navigate = useNavigate();
  const disk = overview.data;
  const volume = disk?.volume ?? null;
  const items = machineAttention(docker.status, volume);
  const checking = (docker.loading && !docker.status) || overview.isPending;

  const startScan = () => {
    scan.mutate(undefined, { onSuccess: () => navigate(machinePath("disk")) });
  };

  return (
    <div className="queue-dashboard">
      <div className="queue-layout">
        <main className="queue-layout-main">
          <DockerQueueBoard
            status={docker.status}
            error={docker.error}
            loading={docker.loading}
            onChanged={docker.reload}
            attention={(ending) => (
              <MachineAttention
                items={items}
                checking={checking}
                disk={{
                  scanned: Boolean(disk?.scan),
                  scanning: disk?.status.state === "running" || scan.isPending,
                  onScan: startScan,
                }}
                diskError={
                  overview.isError ? describeError(overview.error, "The disk overview did not load.") : ""
                }
                onRetryDisk={() => void overview.refetch()}
                onEndLease={ending.onEnd}
                pendingLeaseId={ending.pendingId}
              />
            )}
            meters={
              volume ? (
                <section className="docker-board-pool" aria-label="Disk">
                  <div className="docker-board-meters">
                    <CapacityMeter
                      label="Disk"
                      used={volume.used_bytes}
                      total={volume.total_bytes}
                      format={formatDiskBytes}
                      tone={volumeTone(volume)}
                    />
                  </div>
                </section>
              ) : null
            }
          />
        </main>
      </div>
    </div>
  );
}
