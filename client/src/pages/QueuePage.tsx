import { QueueDashboard } from "../components/QueueDashboard";
import { QueueTopbarControls } from "../components/QueueTopbarControls";
import { PageTopbar } from "../components/TopbarPageSlot";
import { QueueStatusProvider, useQueueStatus } from "../state/QueueStatusContext";

/**
 * The queue screen.
 *
 * The provider sits here rather than above the layout: `PageTopbar` portals
 * its children into the topbar's DOM node, but they stay this component's
 * children in the React tree, so the controls up there read the same context
 * as the body down here — and the page keeps its one socket.
 */
export function QueuePage() {
  return (
    <QueueStatusProvider>
      <QueueScreen />
    </QueueStatusProvider>
  );
}

function QueueScreen() {
  const { workspaces, workspacesLoading } = useQueueStatus();

  return (
    <div className="screen-view screen-view--queue">
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
