import { QueueDashboard } from "../QueueDashboard";
import { useQueueStatus } from "../../state/QueueStatusContext";

/**
 * Machine's Lanes tab: the agent runs holding a lane, and what waits for one.
 *
 * It was the Queue page. Capacity, Lanes and Disk all answer "what is using
 * this machine?", so they are one page with three tabs rather than two pages
 * in the rail.
 *
 * Queue status (and the socket behind it) lives in the app shell so run
 * notifications reach the inbox from every page; this only renders it.
 */
export function MachineLanesView() {
  const { workspaces, workspacesLoading } = useQueueStatus();

  // The board itself is global; it does not wait on a workspace. The empty
  // state is about having nothing to run, not nothing to show.
  if (workspaces.length) return <QueueDashboard />;
  return (
    <div className="queue-page-empty">
      {workspacesLoading ? "Loading workspaces…" : "Add a workspace before agents can run in a lane."}
    </div>
  );
}
