import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { ShipProbe } from "../api/types";
import { AddToTabMenu } from "../components/AddToTabMenu";
import { BaxterAvatar } from "../components/chat/BaxterAvatar";
import {
  ComposerAttachButton,
  ComposerAttachmentTray,
} from "../components/chat/ComposerAttachments";
import { HomeActivity } from "../components/home/HomeActivity";
import { HomeNeedsYou } from "../components/home/HomeNeedsYou";
import { HomeRunningNow } from "../components/home/HomeRunningNow";
import { StudioChatComposer } from "../components/studio/StudioChat";
import { Button } from "../components/ui/Button";
import { useComposerAttachments } from "../hooks/useComposerAttachments";
import { useTicketStatusSummary } from "../hooks/useTicketStatusSummary";
import { ticketPath, type ArtifactTab } from "../lib/appNavigation";
import { chatPath, stashHomeBaxterFiles, stashHomeBaxterPrompt } from "../lib/homeBaxter";
import { failedToday, needsYouRows, pendingApprovals, waitsOnPerson } from "../lib/homeNeedsYou";
import { runningTickets } from "../lib/runningTickets";
import { useQueueStatus } from "../state/QueueStatusContext";
import { peekTicket } from "../state/ticketPeekStore";
import { describeError } from "../state/toastStore";
import { useUiStore } from "../state/uiStore";
import "./HomePage.css";

const HERO_CHIPS = [
  "What should we ship today?",
  "Review what's waiting on me",
  "Triage the stuck tickets",
  "Run a workflow",
] as const;

/**
 * Enough runs to cover a working day in most cases; when it does not, the
 * failed count says "N+" rather than claiming the whole day (see `failedToday`).
 */
const RUN_LIMIT = 100;

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

function loadError(query: { isError: boolean; error: unknown }, what: string): string | null {
  return query.isError
    ? `Couldn't load ${what}: ${describeError(query.error, "request failed")}`
    : null;
}

/**
 * The one line that says GitHub could not be asked, however many workspaces
 * could not be asked.
 *
 * Every probe row is `unavailable` until the first reconcile tick (the ship
 * sweep is `at_startup=False`), so on every server start this would otherwise
 * be the same sentence once per workspace. Distinct reasons are kept — "it
 * failed" must not collapse into one answer that hides a second cause — but
 * they share the single line.
 */
function shipProbeError(probes: readonly ShipProbe[]): string | null {
  const reasons = [
    ...new Set(
      probes
        .filter((probe) => probe.listing_state === "unavailable")
        .map((probe) => describeError(probe.error, "request failed")),
    ),
  ];
  return reasons.length === 0 ? null : `Couldn't load ships: ${reasons.join("; ")}`;
}

/**
 * Home answers, in order: what needs me, what is running, what just happened.
 *
 * It used to lead with a composer and eight equal tiles, the largest of them
 * Backlog (1,386), the least actionable number on the page, while fourteen
 * identical approvals sat in a list below. Now the header carries four small
 * numbers, and the page leads with the work that is waiting on a person.
 */
export function HomePage() {
  const navigate = useNavigate();
  const workspace = useUiStore((s) => s.workspace);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const [draft, setDraft] = useState("");
  const attachments = useComposerAttachments();
  const now = useMemo(() => new Date(), []);

  const workspaceParam = workspace && workspace !== "all" ? workspace : undefined;

  const approvalsQ = useQuery({
    queryKey: ["home-approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 15_000,
  });
  // In progress and blocked for the running cards' stage strips; parked and
  // blocked are where tickets waiting on a person live.
  const ticketsQ = useQuery({
    queryKey: ["home-tickets", workspaceParam],
    queryFn: () =>
      api.tickets({
        workspace: workspaceParam,
        state: ["in_progress", "blocked", "parked"],
      }),
    refetchInterval: 15_000,
  });
  const runsQ = useQuery({
    queryKey: ["home-runs", RUN_LIMIT],
    queryFn: () => api.runs(undefined, RUN_LIMIT),
    refetchInterval: 15_000,
  });
  const shipsQ = useQuery({
    queryKey: ["done-ticket-ship", workspaceParam ?? "all"],
    queryFn: () => api.doneTicketShip(workspaceParam),
    refetchInterval: 15_000,
    placeholderData: (previous) => previous,
  });
  const statusQ = useTicketStatusSummary();
  const queue = useQueueStatus();

  const pending = useMemo(() => pendingApprovals(approvalsQ.data), [approvalsQ.data]);
  const tickets = useMemo(() => ticketsQ.data ?? [], [ticketsQ.data]);
  const ticketsById = useMemo(() => new Map(tickets.map((t) => [t.id, t])), [tickets]);
  const runs = useMemo(() => runsQ.data ?? [], [runsQ.data]);
  const failures = useMemo(() => failedToday(runs, now, RUN_LIMIT), [runs, now]);
  const shipProbes = useMemo(() => {
    const probes = shipsQ.data?.probes ?? [];
    return workspaceParam ? probes.filter((probe) => probe.workspace_slug === workspaceParam) : probes;
  }, [shipsQ.data, workspaceParam]);
  const shipViolations = useMemo(
    () => shipProbes.flatMap((probe) => probe.violations),
    [shipProbes],
  );
  const rows = useMemo(
    () => needsYouRows(pending, failures.runs, tickets, shipViolations),
    [pending, failures.runs, tickets, shipViolations],
  );
  const running = runningTickets(queue.activeRuns);
  const status = statusQ.data;

  const needYou =
    pending.length + tickets.filter(waitsOnPerson).length + new Set(shipViolations.map((v) => v.ticket_id)).size;
  const needsLoading = approvalsQ.isLoading || ticketsQ.isLoading || runsQ.isLoading || shipsQ.isLoading;
  const needsErrors = [
    loadError(approvalsQ, "approvals"),
    loadError(ticketsQ, "tickets"),
    loadError(runsQ, "recent runs"),
    shipsQ.isError
      ? `Couldn't load ships: ${describeError(shipsQ.error, "request failed")}`
      : shipProbeError(shipProbes),
  ].filter((line): line is string => line !== null);
  const needsStatus = shipProbes.some((probe) => probe.listing_state === "incomplete")
    ? ["Ship list may be incomplete — GitHub did not return every PR."]
    : [];

  const openTicket = (ticketId: string, tab?: ArtifactTab) => peekTicket(ticketId, tab);
  const openInbox = () => setInboxOpen(true);

  const sendToBaxter = (text: string) => {
    const content = text.trim();
    const files = attachments.items.map((item) => item.file);
    if (!content && !files.length) return;
    stashHomeBaxterPrompt(content);
    stashHomeBaxterFiles(files);
    setDraft("");
    attachments.clear();
    navigate(chatPath());
  };

  return (
    <div className="home-page lg-chat-surface">
      <header className="home-header">
        <div>
          <p className="home-kicker">{formatDateLine(now)}</p>
          <h1 className="home-greeting">{greetingFor(now)}</h1>
        </div>
        <dl className="home-kpis" aria-label="At a glance">
          <div className="home-kpi home-kpi--you">
            <dt>need you</dt>
            <dd>{needsLoading ? "…" : needYou}</dd>
          </div>
          <div className={`home-kpi${failures.runs.length ? " home-kpi--bad" : ""}`}>
            <dt>failed today</dt>
            <dd>
              {runsQ.isLoading ? "…" : `${failures.runs.length}${failures.complete ? "" : "+"}`}
            </dd>
          </div>
          <div className="home-kpi">
            <dt>running</dt>
            <dd>{status ? status.running : "…"}</dd>
          </div>
          <div className="home-kpi">
            <dt>in progress</dt>
            <dd>
              <Link to="/work-items">{status ? status.in_progress : "…"}</Link>
            </dd>
          </div>
        </dl>
        <div className="home-header-actions">
          {/* The whole board, as a pane: what the old Board status card offered. */}
          <AddToTabMenu
            primitiveId="chat_kanban"
            values={new Map()}
            title="Board"
            label="Add the board to a tab"
          />
          <Link className="home-console-link" to="/console">
            Open Console
          </Link>
        </div>
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
            canSendEmpty={attachments.items.length > 0}
            onFiles={attachments.add}
            accessory={
              <ComposerAttachmentTray items={attachments.items} onRemove={attachments.remove} />
            }
            toolbar={<ComposerAttachButton onFiles={attachments.add} />}
            placeholder="What should we ship today?"
            sendLabel="Ask Baxter"
            variant="dock"
            iconOnlySend={false}
          />
          <div className="lg-chat-chip-row home-chip-row" role="list">
            {HERO_CHIPS.map((chip) => (
              <Button
                key={chip}
                variant="plain"
                className="lg-chat-chip home-chip"
                role="listitem"
                onClick={() => setDraft(chip)}
              >
                {chip}
              </Button>
            ))}
          </div>
        </div>
      </section>

      <div className="home-grid">
        <div className="home-grid-main">
          <HomeNeedsYou
            rows={rows}
            loading={needsLoading}
            error={needsErrors}
            status={needsStatus}
            onOpenApproval={(ticketId) => {
              openInbox();
              if (ticketId) navigate(ticketPath(ticketId));
            }}
            onOpenTicket={openTicket}
            onOpenInbox={openInbox}
          />
          <HomeRunningNow
            runs={running}
            ticketsById={ticketsById}
            lanes={
              queue.loading || queue.error
                ? null
                : {
                    busy: Math.min(queue.stats.active_count, queue.stats.max_concurrent),
                    max: queue.stats.max_concurrent,
                  }
            }
            error={queue.error}
            onOpenTicket={openTicket}
          />
        </div>
        <HomeActivity
          runs={runs}
          now={now}
          loading={runsQ.isLoading}
          error={runsQ.isError ? describeError(runsQ.error, "request failed") : null}
          onOpenTicket={openTicket}
        />
      </div>
    </div>
  );
}
