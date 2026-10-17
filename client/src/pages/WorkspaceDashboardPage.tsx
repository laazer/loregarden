import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import {
  workspaceDashboardApi,
  workspaceDashboardKeys,
  type ActivityHighlightKind,
} from "../api/workspaceDashboardApi";
import { PageTopbar } from "../components/TopbarPageSlot";
import { ActiveTicketsSection } from "../components/workspaceDashboard/ActiveTicketsSection";
import { ActivitySection } from "../components/workspaceDashboard/ActivitySection";
import { InstancesSection } from "../components/workspaceDashboard/InstancesSection";
import { MilestonesSection } from "../components/workspaceDashboard/MilestonesSection";
import { NeedsYouSection } from "../components/workspaceDashboard/NeedsYouSection";
import { PullRequestsSection } from "../components/workspaceDashboard/PullRequestsSection";
import { WorkspaceHeader } from "../components/workspaceDashboard/WorkspaceHeader";
import { Button } from "../components/ui/Button";
import { INSTANCES_KEY } from "../hooks/useLocalInstances";
import { workspacesPath } from "../lib/appNavigation";
import { beginVisit, resolveCatchUpWindow, type CatchUpSpan } from "../lib/catchUpWindow";
import { indexTree } from "../lib/workspaceDashboard";
import "./WorkspaceDashboardPage.css";

/** GitHub is asked again at most this often; every refetch is a `gh` call. */
const PR_STALE_MS = 60_000;

/** The prefixes Refresh invalidates: everything this page reads. */
const PAGE_KEYS = [
  "workspace-remote",
  "workspace-pull-requests",
  "workspace-activity",
  "tickets",
  "ticket-status-summary",
  "ticket-tree",
  "approvals",
  INSTANCES_KEY[0],
];

interface WorkspaceDashboardPageProps {
  workspaceSlug: string;
}

/**
 * One workspace, for a person who lost track of it.
 *
 * Answers "where does this project stand, what needs me, and what do I do
 * first?" in the order a person asks it: what only they can unblock, then the
 * PRs in review order, then what is moving, and on the side what happened
 * while they were away, how far each milestone has got, and what is running.
 * Each section reads its own endpoint, so a slow or failing GitHub leaves the
 * rest usable.
 */
export function WorkspaceDashboardPage({ workspaceSlug }: WorkspaceDashboardPageProps) {
  const queryClient = useQueryClient();
  // Taken once per mount: the anchor must not move while the page is open.
  const [visit] = useState(() => beginVisit(workspaceSlug, new Date()));
  const [now] = useState(() => new Date());
  const [span, setSpan] = useState<CatchUpSpan>("last_visit");
  const [kinds, setKinds] = useState<ActivityHighlightKind[]>([]);
  const catchUp = useMemo(() => resolveCatchUpWindow(span, visit.lastVisit, now), [span, visit.lastVisit, now]);
  const since = catchUp.since.toISOString();

  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.workspaces,
  });
  const workspace = workspaces.data?.find((ws) => ws.slug === workspaceSlug);

  const remote = useQuery({
    queryKey: workspaceDashboardKeys.remote(workspaceSlug),
    queryFn: () => workspaceDashboardApi.remote(workspaceSlug),
    staleTime: 5 * 60_000,
  });
  const pullRequests = useQuery({
    queryKey: workspaceDashboardKeys.pullRequests(workspaceSlug),
    queryFn: () => workspaceDashboardApi.pullRequests(workspaceSlug),
    staleTime: PR_STALE_MS,
    refetchOnWindowFocus: false,
  });
  const activity = useQuery({
    queryKey: workspaceDashboardKeys.activity(workspaceSlug, since, kinds),
    queryFn: () => workspaceDashboardApi.activity(workspaceSlug, since, kinds),
    placeholderData: (previous) => previous,
  });
  const summary = useQuery({
    queryKey: ["ticket-status-summary", workspaceSlug],
    queryFn: () => api.ticketStatusSummary(workspaceSlug),
  });
  const active = useQuery({
    queryKey: ["tickets", "workspace-dashboard", workspaceSlug, "in_progress"],
    queryFn: () => api.tickets({ workspace: workspaceSlug, state: "in_progress" }),
  });
  const stuck = useQuery({
    queryKey: ["tickets", "workspace-dashboard", workspaceSlug, "stuck"],
    queryFn: () => api.tickets({ workspace: workspaceSlug, state: ["blocked", "parked"] }),
  });
  // The inbox's own query and cadence; filtered here rather than asked per workspace.
  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 5000,
    select: (rows) => rows.filter((approval) => approval.workspace_slug === workspaceSlug),
  });
  const milestones = useQuery({
    // Unfiltered: a milestone-filtered tree comes back without children.
    queryKey: ["ticket-tree", "workspace-dashboard", workspaceSlug],
    queryFn: () => api.ticketTree({ workspace: workspaceSlug }),
  });
  const ticketIndex = useMemo(() => indexTree(milestones.data ?? []), [milestones.data]);

  // Its own state, not `useIsFetching`: approvals and instances poll every few
  // seconds, which kept the button reading "Refreshing…" for good.
  const [refreshing, setRefreshing] = useState(false);
  const refresh = () => {
    setRefreshing(true);
    void Promise.allSettled(PAGE_KEYS.map((key) => queryClient.invalidateQueries({ queryKey: [key] }))).finally(() =>
      setRefreshing(false),
    );
  };

  if (workspaces.isSuccess && !workspace) {
    return (
      <div className="screen-view screen-view--workspace-dashboard">
        <PageTopbar title="Workspace not found" />
        <div className="wsd-body">
          <p className="wsd-section-empty">
            No active workspace is called “{workspaceSlug}”. It may be archived or renamed.{" "}
            <Link to={workspacesPath("workspaces")}>See every workspace</Link>.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="screen-view screen-view--workspace-dashboard">
      <PageTopbar title="Workspace">
        <Link to={workspacesPath("workspaces")} className="topbar-page-note">
          All workspaces
        </Link>
        <Button
          variant="secondary"
          className="topbar-page-btn"
          disabled={refreshing}
          aria-busy={refreshing}
          onClick={refresh}
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </Button>
      </PageTopbar>

      <div className="wsd-body">
        <WorkspaceHeader
          workspaceSlug={workspaceSlug}
          name={workspace?.name ?? workspaceSlug}
          remote={remote}
          summary={summary}
          approvals={approvals}
          pullRequests={pullRequests}
        />
        <div className="wsd-columns">
          <NeedsYouSection ticketIndex={ticketIndex} approvals={approvals} stuck={stuck} pullRequests={pullRequests} />
          <ActivitySection
            query={activity}
            span={span}
            window={catchUp}
            remembered={visit.remembered}
            kinds={kinds}
            onSpanChange={setSpan}
            onKindsChange={setKinds}
            now={now}
          />
        </div>
        <PullRequestsSection query={pullRequests} now={now} />
        <div className="wsd-columns">
          <ActiveTicketsSection query={active} />
          <div className="wsd-column">
            <MilestonesSection query={milestones} />
            <InstancesSection workspaceSlug={workspaceSlug} now={now} />
          </div>
        </div>
      </div>
    </div>
  );
}
