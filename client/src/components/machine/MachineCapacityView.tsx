import { useState } from "react";

import { useDockerCapacity } from "../../hooks/useDockerCapacity";
import { Button } from "../ui/Button";
import { DockerAttentionRail } from "../DockerAttentionRail";
import { DockerCapacityRail } from "../DockerCapacityRail";
import { DockerQueueBoard } from "../DockerQueueBoard";
import "../QueueDashboard.css";

type RailTab = "capacity" | "attention";

const RAIL_TABS: { key: RailTab; label: string }[] = [
  { key: "capacity", label: "Capacity" },
  { key: "attention", label: "Attention" },
];

/**
 * Who holds the machine's CPU and memory, who waits behind them, and what
 * needs a person: the board that used to be the Queue page's "Machine" toggle.
 *
 * One `useDockerCapacity` read feeds the board and the rail, so the two halves
 * of the screen cannot disagree about the same moment. It polls only while this
 * view is mounted, which is now true only on this tab.
 */
export function MachineCapacityView() {
  const docker = useDockerCapacity();
  const [tab, setTab] = useState<RailTab>("capacity");

  return (
    <div className="queue-dashboard">
      <div className="queue-layout">
        <main className="queue-layout-main">
          <DockerQueueBoard
            status={docker.status}
            error={docker.error}
            loading={docker.loading}
            onChanged={docker.reload}
          />
        </main>
        <aside className="queue-rail">
          <div className="queue-rail-card">
            <div className="queue-rail-tabs tab-bar">
              <div
                className="tab-bar-scroll"
                role="tablist"
                aria-label="Machine capacity panels"
              >
                {RAIL_TABS.map((option) => (
                  <Button
                    key={option.key}
                    variant="plain"
                    role="tab"
                    aria-selected={tab === option.key}
                    className={`tab-btn${tab === option.key ? " active" : ""}`}
                    onClick={() => setTab(option.key)}
                  >
                    {option.label}
                  </Button>
                ))}
              </div>
            </div>
            <div className="queue-rail-body">
              {tab === "capacity" ? (
                <DockerCapacityRail
                  status={docker.status}
                  error={docker.error}
                  loading={docker.loading}
                />
              ) : (
                <DockerAttentionRail status={docker.status} />
              )}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
