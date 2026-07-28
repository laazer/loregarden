import { useQuery } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, type Approval, type TicketSummary } from "../api/client";
import { BaxterAvatar } from "../components/chat/BaxterAvatar";
import { StudioChatComposer } from "../components/studio/StudioChat";
import { studioTicketSessionNewPath, ticketPath } from "../lib/appNavigation";
import { HOME_BAXTER_BRIEF_KEY } from "../lib/homeBaxter";
import { useUiStore } from "../state/uiStore";
import "./HomePage.css";

const HERO_CHIPS = [
  "What should we ship today?",
  "Review what's waiting on me",
  "Triage the stuck tickets",
  "Run a workflow",
] as const;

function pendingApprovals(approvals: Approval[] | undefined): Approval[] {
  return (approvals ?? []).filter((a) => !a.status || a.status === "pending");
}

function greetingFor(now: Date): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function formatDateLine(now: Date): string {
  return now.toLocaleDateString(undefined, {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
}

export function HomePage() {
  const navigate = useNavigate();
  const workspace = useUiStore((s) => s.workspace);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const [draft, setDraft] = useState("");
  const now = useMemo(() => new Date(), []);

  const workspaceParam = workspace && workspace !== "all" ? workspace : undefined;

  const approvalsQ = useQuery({
    queryKey: ["home-approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 15_000,
  });
  const ticketsQ = useQuery({
    queryKey: ["home-tickets", workspaceParam],
    queryFn: () =>
      api.tickets({
        workspace: workspaceParam,
        state: ["in_progress", "blocked"],
      }),
    refetchInterval: 15_000,
  });
  const workflowsQ = useQuery({
    queryKey: ["home-workflows"],
    queryFn: api.studioWorkflows,
    refetchInterval: 60_000,
  });
  const runsQ = useQuery({
    queryKey: ["home-runs"],
    queryFn: () => api.runs(),
    refetchInterval: 15_000,
  });

  const pending = pendingApprovals(approvalsQ.data);
  const tickets = ticketsQ.data ?? [];
  const activeTickets = useMemo(() => {
    const rank = (t: TicketSummary) => (t.state === "blocked" ? 0 : 1);
    return [...tickets].sort(
      (a, b) => rank(a) - rank(b) || a.priority - b.priority || a.title.localeCompare(b.title),
    );
  }, [tickets]);
  const inProgress = tickets.filter((t) => t.state === "in_progress");
  const blocked = tickets.filter((t) => t.state === "blocked");
  const ticketsLoading = ticketsQ.isLoading || (ticketsQ.isFetching && !ticketsQ.data);
  const workflows = workflowsQ.data ?? [];
  const featuredWorkflows = workflows.slice(0, 6);
  const publishedCount = workflows.filter((w) => Boolean(w.published_template_id)).length;
  const activity = (runsQ.data ?? []).slice(0, 8);

  const summaryLine = useMemo(() => {
    const parts: string[] = [];
    if (pending.length) parts.push(`${pending.length} approval${pending.length === 1 ? "" : "s"} waiting`);
    if (blocked.length) parts.push(`${blocked.length} blocked`);
    if (inProgress.length) parts.push(`${inProgress.length} in progress`);
    if (!parts.length) parts.push("Nothing urgent — ask Baxter what to do next");
    return parts.join(" · ");
  }, [pending.length, blocked.length, inProgress.length]);

  const sendToBaxter = (text: string) => {
    const content = text.trim();
    if (!content) return;
    try {
      sessionStorage.setItem(HOME_BAXTER_BRIEF_KEY, content);
    } catch {
      /* private mode — Studio still opens; brief just won't prefill */
    }
    setDraft("");
    navigate(studioTicketSessionNewPath());
  };

  return (
    <div className="home-page">
      <header className="home-header">
        <div className="home-header-text">
          <p className="home-kicker">{formatDateLine(now)}</p>
          <h1 className="home-greeting">{greetingFor(now)}</h1>
          <p className="home-summary">{summaryLine}</p>
        </div>
        <Link className="home-console-link" to="/console">
          Open Console
        </Link>
      </header>

      <section className="home-hero" aria-label="Ask Baxter">
        <div className="home-hero-avatar">
          <BaxterAvatar state="idle" label="Baxter" />
        </div>
        <div className="home-hero-body">
          <StudioChatComposer
            value={draft}
            onChange={setDraft}
            onSubmit={() => sendToBaxter(draft)}
            placeholder="What should we ship today?"
            sendLabel="Ask Baxter"
          />
          <div className="home-chip-row" role="list">
            {HERO_CHIPS.map((chip) => (
              <button
                key={chip}
                type="button"
                className="home-chip"
                role="listitem"
                onClick={() => setDraft(chip)}
              >
                {chip}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="home-cards" aria-label="Live status">
        <HomeCard
          title="Approvals waiting"
          count={pending.length}
          empty="Inbox is clear"
          actionLabel="Open inbox"
          onAction={() => {
            setInboxOpen(true);
            navigate("/console");
          }}
        >
          {pending.slice(0, 4).map((a) => (
            <button
              key={a.id}
              type="button"
              className="home-row"
              onClick={() => {
                setInboxOpen(true);
                navigate(ticketPath(a.ticket_id, "triage"));
              }}
            >
              <span className="home-row-title">{a.title || a.tool_name || "Approval"}</span>
              <span className="home-row-meta">{a.ticket_external_id || a.kind}</span>
            </button>
          ))}
        </HomeCard>

        <HomeCard
          title="Workflows"
          count={publishedCount || workflows.length}
          empty="No studio workflows yet"
          actionLabel="Open Studios"
          onAction={() => navigate("/studio/workflows")}
        >
          {featuredWorkflows.slice(0, 4).map((w) => (
            <button
              key={w.slug}
              type="button"
              className="home-row"
              onClick={() => navigate(`/studio/workflows/${encodeURIComponent(w.slug)}`)}
            >
              <span className="home-row-title">{w.name || w.slug}</span>
              <span className="home-row-meta">
                {w.published_template_id ? "published" : "draft"}
              </span>
            </button>
          ))}
        </HomeCard>

        <HomeCard
          title="Tickets in progress"
          count={ticketsLoading ? null : inProgress.length + blocked.length}
          empty={ticketsLoading ? "Loading…" : "No active tickets"}
          actionLabel="Open Console"
          onAction={() => navigate("/console")}
        >
          {activeTickets.slice(0, 8).map((t: TicketSummary) => (
            <button
              key={t.id}
              type="button"
              className="home-row"
              onClick={() => navigate(ticketPath(t.id, "diff"))}
            >
              <span className="home-row-title">{t.title}</span>
              <span className={`home-row-meta home-row-meta--${t.state}`}>{t.state}</span>
            </button>
          ))}
        </HomeCard>
      </section>

      <section className="home-activity" aria-label="Recent activity">
        <div className="home-activity-head">
          <h2>Recent activity</h2>
        </div>
        {activity.length === 0 ? (
          <p className="home-empty">No recent runs.</p>
        ) : (
          <ul className="home-activity-list">
            {activity.map((run) => (
              <li key={run.id} className="home-activity-item">
                <span className={`home-run-status home-run-status--${run.status}`}>{run.status}</span>
                <span className="home-activity-main">
                  <span className="home-activity-agent">{run.agent_id || "agent"}</span>
                  <span className="home-activity-stage">{run.stage_key || run.run_code || run.id.slice(0, 8)}</span>
                </span>
                <span className="home-activity-cmd" title={run.command}>
                  {run.command || "—"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function HomeCard({
  title,
  count,
  empty,
  actionLabel,
  onAction,
  children,
}: {
  title: string;
  count: number | null;
  empty: string;
  actionLabel: string;
  onAction: () => void;
  children: ReactNode;
}) {
  const childCount = Array.isArray(children)
    ? children.filter(Boolean).length
    : children
      ? 1
      : 0;

  return (
    <article className="home-card">
      <header className="home-card-head">
        <h2>{title}</h2>
        <span className="home-card-count">{count === null ? "…" : count}</span>
      </header>
      <div className="home-card-body">
        {childCount > 0 ? children : <p className="home-empty">{empty}</p>}
      </div>
      <footer className="home-card-foot">
        <button type="button" className="home-card-action" onClick={onAction}>
          {actionLabel}
        </button>
      </footer>
    </article>
  );
}
