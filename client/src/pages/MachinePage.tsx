import { useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { DiskUsageView } from "../components/machine/DiskUsageView";
import { MachineCapacityView } from "../components/machine/MachineCapacityView";
import { MachineLanesView } from "../components/machine/MachineLanesView";
import { QueueTopbarControls } from "../components/QueueTopbarControls";
import { LocalInstancesModal } from "../components/LocalInstancesModal";
import { PageTopbar } from "../components/TopbarPageSlot";
import { UsageModal } from "../components/UsageModal";
import { Button } from "../components/ui/Button";
import { useUsageSnapshot } from "../hooks/useUsageSnapshot";
import {
  MACHINE_TABS,
  machinePath,
  machineTabFromPath,
  type MachineTab,
} from "../lib/appNavigation";
import "./MachinePage.css";

const TAB_LABELS: Record<MachineTab, string> = {
  capacity: "Capacity",
  lanes: "Lanes",
  disk: "Disk",
};

const TAB_NOTES: Record<MachineTab, string> = {
  capacity: "CPU and memory for test runs, builds and containers",
  lanes: "Agent runs holding a lane, and what waits for one",
  disk: "What is using the disk, and what can safely go",
};

/**
 * The machine every workspace shares: its CPU and memory (Capacity), the agent
 * runs holding its lanes (Lanes, once the Queue page), and its disk (Disk).
 * Three tabs in the URL, `/machine`, `/machine/lanes` and `/machine/disk`, so a
 * link from a toast or a chat answer opens the right one.
 *
 * Local instances (branch servers on their own ports) and provider usage open
 * from here too. They used to sit in the topbar of every page; both are about
 * what this machine is running and spending, and the rail flags Machine when
 * usage nears a limit, so the warning is not lost with the button.
 */
export function MachinePage() {
  const location = useLocation();
  const tab = machineTabFromPath(location.pathname);
  const [instancesOpen, setInstancesOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const usage = useUsageSnapshot();
  const nearLimit = Boolean(usage.query.data?.near_limit);

  return (
    <div className="screen-view screen-view--machine">
      <PageTopbar title="Machine">
        <span className="topbar-page-note">{TAB_NOTES[tab]}</span>
        {tab === "lanes" ? <QueueTopbarControls /> : null}
        <Button
          variant="secondary"
          compact
          onClick={() => setInstancesOpen(true)}
          title="Branch servers and clients on their own ports"
        >
          Instances
        </Button>
        <Button
          variant="secondary"
          compact
          className={nearLimit ? "usage-btn-warning" : undefined}
          aria-label={nearLimit ? "Usage — limits are getting close" : "Usage"}
          onClick={() => setUsageOpen(true)}
        >
          Usage
          {nearLimit ? (
            <span className="usage-alert-badge" aria-hidden="true">
              !
            </span>
          ) : null}
        </Button>
      </PageTopbar>
      <div className="machine-page-body">
        <nav className="machine-tabs" aria-label="Machine views">
          {MACHINE_TABS.map((option) => (
            <Link
              key={option}
              to={machinePath(option)}
              className={`machine-tab${option === tab ? " active" : ""}`}
              aria-current={option === tab ? "page" : undefined}
            >
              {TAB_LABELS[option]}
            </Link>
          ))}
        </nav>
        {tab === "disk" ? <DiskUsageView /> : tab === "lanes" ? <MachineLanesView /> : <MachineCapacityView />}
      </div>
      <LocalInstancesModal open={instancesOpen} onClose={() => setInstancesOpen(false)} />
      <UsageModal
        open={usageOpen}
        snapshot={usage.query.data}
        isLoading={usage.query.isFetching}
        error={usage.query.error}
        onClose={() => setUsageOpen(false)}
        onRefresh={usage.refresh}
      />
    </div>
  );
}
