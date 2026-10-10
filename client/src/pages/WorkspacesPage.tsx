import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useLocation } from "react-router-dom";

import { InstancesTab } from "../components/workspaces/InstancesTab";
import { WorkspacesTab } from "../components/workspaces/WorkspacesTab";
import { PageTopbar } from "../components/TopbarPageSlot";
import { INSTANCES_KEY, INTEGRATION_KEY } from "../hooks/useLocalInstances";
import { WORKSPACES_TABS, workspacesPath, workspacesTabFromPath, type WorkspacesTab as Tab } from "../lib/appNavigation";
import "../components/LocalInstancesModal.css";
import "./WorkspacesPage.css";

const TAB_LABELS: Record<Tab, string> = {
  workspaces: "Workspaces",
  instances: "Instances",
};

/** Everything either tab shows; `["workspaces"]` is the app-wide workspace list. */
const REFRESHED = [["workspaces"], INSTANCES_KEY, INTEGRATION_KEY];

/**
 * The workspaces loregarden manages, and the servers running from them.
 *
 * Two tabs, in the URL: the workspace list (add, set up, archive) and the
 * running instances (watch, stop, launch). The topbar modal is the quick view
 * of the second.
 */
export function WorkspacesPage() {
  const location = useLocation();
  const tab = workspacesTabFromPath(location.pathname);
  const queryClient = useQueryClient();
  const fetching = useIsFetching({ predicate: (q) => REFRESHED.some((key) => q.queryKey[0] === key[0]) }) > 0;

  return (
    <div className="screen-view screen-view--workspaces">
      <PageTopbar title="Workspaces">
        <span className="topbar-page-note">Repositories loregarden works in, and what runs from them</span>
        <button
          type="button"
          className="btn-secondary topbar-page-btn"
          disabled={fetching}
          onClick={() => {
            for (const queryKey of REFRESHED) void queryClient.invalidateQueries({ queryKey });
          }}
        >
          {fetching ? "Refreshing…" : "Refresh"}
        </button>
      </PageTopbar>

      <div className="instances-page-body">
        <nav className="workspaces-tabs" aria-label="Workspaces views">
          {WORKSPACES_TABS.map((option) => (
            <Link
              key={option}
              to={workspacesPath(option)}
              className={`workspaces-tab${option === tab ? " active" : ""}`}
              aria-current={option === tab ? "page" : undefined}
            >
              {TAB_LABELS[option]}
            </Link>
          ))}
        </nav>
        {tab === "instances" ? <InstancesTab /> : <WorkspacesTab />}
      </div>
    </div>
  );
}

/** `/instances` was this page's old home; links and bookmarks still land on its tab. */
export function LegacyInstancesRedirect() {
  return <Navigate replace to={workspacesPath("instances")} />;
}
