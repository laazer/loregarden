import { QueueDashboard } from "../components/QueueDashboard";
import { QueueTopbarControls } from "../components/QueueTopbarControls";
import { PageTopbar } from "../components/TopbarPageSlot";
import { useQueueStatus } from "../state/QueueStatusContext";

/**
 * The queue screen.
 *
 * Queue status (and the socket behind it) lives in the app shell so run
 * notifications reach the inbox from every page. PageTopbar still portals its
 * children into the topbar DOM while keeping them under this tree for context.
 */
export function QueuePage() {
  return <QueueScreen />;
}

function QueueScreen() {
  const { workspaces, workspacesLoading } = useQueueStatus();

  return (
    <div className="screen-view">
      <PageTopbar title="Queue Dashboard">
        <QueueTopbarControls />
      </PageTopbar>

      <div className="queue-page-body">
        {/* The board itself is global — it does not wait on a workspace. The
            empty state is about having nothing to run, not nothing to show. */}
        {workspaces.length ? (
          <QueueDashboard />
        ) : workspacesLoading ? (
          <div className="queue-page-empty">Loading workspaces…</div>
        ) : (
          <div className="queue-page-empty">
            Add a workspace in the IDE before using the queue dashboard.
          </div>
        )}
      </div>
    </div>
  );
}
