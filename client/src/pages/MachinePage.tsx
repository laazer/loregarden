import { Link, useLocation } from "react-router-dom";

import { DiskUsageView } from "../components/machine/DiskUsageView";
import { MachineCapacityView } from "../components/machine/MachineCapacityView";
import { PageTopbar } from "../components/TopbarPageSlot";
import {
  MACHINE_TABS,
  machinePath,
  machineTabFromPath,
  type MachineTab,
} from "../lib/appNavigation";
import "./MachinePage.css";

const TAB_LABELS: Record<MachineTab, string> = {
  capacity: "Capacity",
  disk: "Disk",
};

const TAB_NOTES: Record<MachineTab, string> = {
  capacity: "CPU and memory for test runs, builds and containers",
  disk: "What is using the disk, and what can safely go",
};

/**
 * The machine every workspace shares: its CPU and memory (Capacity) and its
 * disk (Disk). Two tabs in the URL, `/machine` and `/machine/disk`, so a link
 * from a toast or a chat answer opens the right one.
 */
export function MachinePage() {
  const location = useLocation();
  const tab = machineTabFromPath(location.pathname);

  return (
    <div className="screen-view screen-view--machine">
      <PageTopbar title="Machine">
        <span className="topbar-page-note">{TAB_NOTES[tab]}</span>
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
        {tab === "disk" ? <DiskUsageView /> : <MachineCapacityView />}
      </div>
    </div>
  );
}
