import { useQuery } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  api,
  type Approval,
  type TicketStatusSummary,
  type TicketSummary,
} from "../api/client";
import { BaxterAvatar } from "../components/chat/BaxterAvatar";
import { StudioChatComposer } from "../components/studio/StudioChat";
import { ticketPath } from "../lib/appNavigation";
import { chatPath, stashHomeBaxterPrompt } from "../lib/homeBaxter";
import { ticketActivityColor, ticketActivityLabel, ticketStateLabel } from "../lib/ticketStates";
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
  const runsQ = useQuery({
    queryKey: ["home-runs"],
    queryFn: () => api.runs(),
    refetchInterval: 15_000,
  });
  const statusQ = useQuery({
    queryKey: ["home-status-summary", workspaceParam],
    queryFn: () => api.ticketStatusSummary(workspaceParam),
    // Faster than the lists: this is the card people watch to see whether
    // anything is actually moving.
    refetchInterval: 10_000,
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
  // Named for what it is: recent runs. "Activity" now means ticket activity.
  const recentRuns = (runsQ.data ?? []).slice(0, 8);
  // Only covers active tickets, since that's what the page already fetches —
  // a run against a ticket that's since finished just falls back to its id.
  const ticketTitleById = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of tickets) map.set(t.id, t.title);
    return map;
  }, [tickets]);

  const status = statusQ.data;
  const summaryLine = useMemo(() => {
    const parts: string[] = [];
    if (pending.length) parts.push(`${pending.length} approval${pending.length === 1 ? "" : "s"} waiting`);
    // Ahead of the in-progress count deliberately: "running" is the number that
    // says whether the machine is busy, and it is usually much smaller.
    if (status?.running) parts.push(`${status.running} running`);
    if (blocked.length) parts.push(`${blocked.length} blocked`);
    if (inProgress.length) parts.push(`${inProgress.length} in progress`);
    if (!parts.length) parts.push("Nothing urgent — ask Baxter what to do next");
    return parts.join(" · ");
  }, [pending.length, blocked.length, inProgress.length, status?.running]);

  const sendToBaxter = (text: string) => {
    const content = text.trim();
    if (!content) return;
    stashHomeBaxterPrompt(content);
    setDraft("");
    navigate(chatPath());
  };

  return (
    <div className="home-page lg-chat-surface">
      <header className="home-header">
        <div >
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
          <BaxterAvatar variant="head" state="idle" size={64} label="Baxter" />
        </div>
        <div className="home-hero-body">
          <StudioChatComposer
            value={draft}
            onChange={setDraft}
            onSubmit={() => sendToBaxter(draft)}
            placeholder="What should we ship today?"
            sendLabel="Ask Baxter"
            variant="dock"
            iconOnlySend={false}
          />
          <div className="lg-chat-chip-row home-chip-row" role="list">
            {HERO_CHIPS.map((chip) => (
              <button
                key={chip}
                type="button"
                className="lg-chat-chip home-chip"
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
        <StatusCard
          summary={status}
          loading={statusQ.isLoading}
          onAction={() => navigate("/console")}
        />

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
                navigate(ticketPath(a.ticket_id));
              }}
            >
              <span className="home-row-title">{a.title || a.tool_name || "Approval"}</span>
              <span className="home-row-meta">{a.ticket_external_id || a.kind}</span>
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
              <span className="home-row-tags">
                <span className={`home-row-meta home-row-meta--${t.state}`}>
                  {ticketStateLabel(t.state)}
                </span>
                {/* The state above says the ticket is open; this says whether
                    anything is happening on it. */}
                <span
                  className="home-row-activity"
                  style={{ color: ticketActivityColor(t.activity) }}
                >
                  {ticketActivityLabel(t.activity)}
                </span>
              </span>
            </button>
          ))}
        </HomeCard>
      </section>

      <section className="home-activity" aria-label="Recent activity">
        <div className="home-activity-head">
          <h2>Recent activity</h2>
        </div>
        {recentRuns.length === 0 ? (
          <p className="home-empty">No recent runs.</p>
        ) : (
          <ul className="home-activity-list">
            {recentRuns.map((run) => {
              const ticketTitle = run.ticket_id ? ticketTitleById.get(run.ticket_id) : undefined;
              const content = (
                <>
                  <span className={`home-run-status home-run-status--${run.status}`}>{run.status}</span>
                  <span className="home-activity-main">
                    <span className="home-activity-agent">{run.agent_id || "agent"}</span>
                    <span className="home-activity-stage">
                      {run.stage_key || run.run_code || run.id.slice(0, 8)}
                    </span>
                    {run.ticket_id && (
                      <span className="home-activity-ticket">{ticketTitle || `Ticket ${run.ticket_id.slice(0, 8)}`}</span>
                    )}
                  </span>
                  <span className="home-activity-cmd" title={run.command}>
                    {run.command || "—"}
                  </span>
                </>
              );
              return (
                <li key={run.id} className="home-activity-item">
                  {run.ticket_id ? (
                    <button
                      type="button"
                      className="home-activity-row"
                      onClick={() => navigate(ticketPath(run.ticket_id as string))}
                    >
                      {content}
                    </button>
                  ) : (
                    <span className="home-activity-row home-activity-row--static">{content}</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

/**
 * The two axes of the board, side by side.
 *
 * They are not the same question, and conflating them is what made the board
 * read as busy when it was not: "in progress" counts open work, "running"
 * counts work with an agent on it. The gap between them is `idle` — the pile
 * that needs a human to press go.
 */
function StatusCard({
  summary,
  loading,
  onAction,
}: {
  summary: TicketStatusSummary | undefined;
  loading: boolean;
  onAction: () => void;
}) {
  const activity: { key: string; label: string; value: number | null }[] = [
    { key: "running", label: "Running", value: summary?.running ?? null },
    { key: "awaiting", label: "Awaiting", value: summary?.awaiting ?? null },
    { key: "queued", label: "Queued", value: summary?.queued ?? null },
    { key: "idle", label: "Idle", value: summary?.idle ?? null },
  ];
  const states: { key: string; label: string; value: number | null }[] = [
    { key: "backlog", label: "Backlog", value: summary?.backlog ?? null },
    { key: "in_progress", label: "In progress", value: summary?.in_progress ?? null },
    { key: "blocked", label: "Blocked", value: summary?.blocked ?? null },
    { key: "done", label: "Done", value: summary?.done ?? null },
  ];
  const open = summary ? summary.backlog + summary.in_progress + summary.blocked : null;

  return (
    <article className="home-card" aria-label="Board status">
      <header className="home-card-head">
        <h2>Board status</h2>
        <span className="home-card-count">{open === null ? "…" : `${open} open`}</span>
      </header>
      <div className="home-card-body home-status-body">
        {!summary && !loading ? (
          <p className="home-empty">Status unavailable</p>
        ) : (
          <>
            <div className="home-status-group">
              <p className="home-status-label">Activity</p>
              <dl className="home-status-grid">
                {activity.map((stat) => (
                  <div key={stat.key} className="home-stat" data-activity={stat.key}>
                    <dt>{stat.label}</dt>
                    <dd style={{ color: ticketActivityColor(stat.key) }}>
                      {stat.value === null ? "…" : stat.value}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
            <div className="home-status-group">
              <p className="home-status-label">State</p>
              <dl className="home-status-grid">
                {states.map((stat) => (
                  <div key={stat.key} className="home-stat" data-state={stat.key}>
                    <dt>{stat.label}</dt>
                    <dd>{stat.value === null ? "…" : stat.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </>
        )}
      </div>
      <footer className="home-card-foot">
        <button type="button" className="home-card-action" onClick={onAction}>
          Open Console
        </button>
      </footer>
    </article>
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
