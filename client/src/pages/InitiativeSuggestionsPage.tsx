import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { InitiativeSuggestionSet } from "../api/client";
import { ApiError } from "../api/http";
import { ConfirmCreate } from "../components/initiatives/suggest/ConfirmCreate";
import { SprintCard } from "../components/initiatives/suggest/SprintCard";
import { ThemeCard } from "../components/initiatives/suggest/ThemeCard";
import { UngroupedMilestones } from "../components/initiatives/suggest/UngroupedMilestones";
import {
  membersOf,
  milestonesOf,
  plannedInitiatives,
  reconcile,
  withCustomTheme,
  type ReviewState,
} from "../components/initiatives/suggest/suggestionEdit";
import { PageTopbar } from "../components/TopbarPageSlot";
import { Button } from "../components/ui/Button";
import { Select } from "../components/ui/Select";
import { navigateToInitiative, navigateToPage } from "../lib/useAppNavigation";
import { describeError, pushToast } from "../state/toastStore";
import "../components/initiatives/Initiatives.css";

const SPRINT_WEEKS = [1, 2, 3, 4] as const;

type Source = InitiativeSuggestionSet["source"];

const suggestionsQueryKey = (days: number) => ["initiative-suggestions", days] as const;

function isAbort(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
}

/** Seconds since `running` turned true, ticking while it stays true. */
function useElapsed(running: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!running) return;
    setSeconds(0);
    const started = Date.now();
    const timer = window.setInterval(() => setSeconds(Math.round((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  return seconds;
}

/** Shown when no sprint is suggested but sprint-style initiatives already hold the pace. */
function NoSprintNote({ data }: { data: InitiativeSuggestionSet }) {
  const { committed, days } = data.sprint;
  if (data.suggestions.some((s) => s.kind === "sprint") || committed === 0) return null;
  return (
    <p className="initiative-hint">
      No new sprint suggested: {committed} work items already sit in a sprint-style initiative, which uses up the pace
      this {days}-day window can absorb.
    </p>
  );
}

/**
 * The suggestions as the operator edits them. Edits live in the page, one set
 * per source, and are reconciled onto each redraw — so a new sprint length, a
 * refetch after a conflict, or a trip to the agent's grouping and back loses
 * nothing still applicable.
 */
function SuggestionReview({
  data,
  review,
  creating,
  onChange,
  onCreate,
}: {
  data: InitiativeSuggestionSet;
  review: ReviewState;
  creating: boolean;
  onChange: (next: ReviewState) => void;
  onCreate: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const milestones = milestonesOf(data);
  const sprint = data.suggestions.find((s) => s.kind === "sprint");
  const planned = plannedInitiatives(data, review);
  const untitled = planned.some((p) => p.title === "");
  const itemCount = planned.reduce((n, p) => n + p.item_ids.length, 0);
  const move = (milestoneId: string, key: string | null) =>
    onChange({ ...review, assign: { ...review.assign, [milestoneId]: key } });

  return (
    <>
      {review.themes.map((theme) => (
        <ThemeCard
          key={theme.key}
          theme={theme}
          members={membersOf(review, theme.key, milestones)}
          themes={review.themes}
          disabled={creating || confirming}
          onChange={(next) =>
            onChange({ ...review, themes: review.themes.map((t) => (t.key === theme.key ? next : t)) })
          }
          onMove={move}
        />
      ))}
      {sprint && review.sprint && (
        <SprintCard
          sprint={sprint}
          sizing={data.sprint}
          edit={review.sprint}
          disabled={creating || confirming}
          onChange={(next) => onChange({ ...review, sprint: next })}
        />
      )}
      <UngroupedMilestones
        milestones={milestones.filter((m) => review.assign[m.id] === null)}
        themes={review.themes}
        disabled={creating || confirming}
        onAssign={move}
        onStartTheme={(m) => onChange(withCustomTheme(review, m))}
      />

      {confirming ? (
        <ConfirmCreate
          planned={planned}
          creating={creating}
          onBack={() => setConfirming(false)}
          onConfirm={onCreate}
        />
      ) : (
        <footer className="initiative-suggest-foot">
          <span className="initiative-muted">
            {untitled
              ? "Every initiative you keep needs a title."
              : planned.length === 0
                ? "Tick a suggestion to create it — keyword themes start unticked until you have read them."
                : `${planned.length} ${planned.length === 1 ? "initiative" : "initiatives"}, ${itemCount} items.`}
          </span>
          <Button
            variant="primary"
            disabled={creating || planned.length === 0 || untitled}
            onClick={() => setConfirming(true)}
          >
            Review and create…
          </Button>
        </footer>
      )}
    </>
  );
}

/**
 * "How could my open work be grouped into initiatives, and what fits in the
 * next sprint?" Reached from the Initiatives page. It opens on an instant
 * keyword draft with only the sprint ticked; the agent regroup is one click
 * away when the words are not enough. Nothing is written until the operator
 * confirms, and then only what is still ticked.
 */
export function InitiativeSuggestionsPage() {
  const qc = useQueryClient();
  const [weeks, setWeeks] = useState<number>(2);
  const days = weeks * 7;
  const [agentResult, setAgentResult] = useState<InitiativeSuggestionSet | null>(null);
  const [reviews, setReviews] = useState<Partial<Record<Source, ReviewState>>>({});
  const [stopped, setStopped] = useState(false);
  const controller = useRef<AbortController | null>(null);

  // Leaving the page stops waiting for the agent; nothing is kept from a turn nobody sees.
  useEffect(() => () => controller.current?.abort(), []);

  const draft = useQuery({
    queryKey: suggestionsQueryKey(days),
    queryFn: () => api.initiativeSuggestions(days),
    meta: { errorTitle: "Load suggestions" },
  });

  const regroup = useMutation({
    // The failure is rendered beside the button, where the operator is looking.
    meta: { suppressErrorToast: true },
    mutationFn: () => {
      controller.current?.abort();
      controller.current = new AbortController();
      setStopped(false);
      return api.agentInitiativeSuggestions(days, controller.current.signal);
    },
    onSuccess: setAgentResult,
    onError: (error) => setStopped(isAbort(error)),
  });
  const elapsed = useElapsed(regroup.isPending);

  const shown = agentResult ?? draft.data;
  const review = shown ? reconcile(reviews[shown.source], shown) : undefined;

  const create = useMutation({
    meta: { errorTitle: "Create initiatives" },
    mutationFn: () => {
      if (!shown || !review) throw new Error("Nothing to create");
      const planned = plannedInitiatives(shown, review);
      return api.applyInitiativeSuggestions(
        planned.map(({ title, description, item_ids, target_date }) => ({ title, description, item_ids, target_date })),
      ).then((result) => ({ result, planned }));
    },
    onSuccess: ({ result: { created }, planned }) => {
      pushToast({
        tone: "success",
        title: created.length === 1 ? "Initiative created" : `${created.length} initiatives created`,
        message: created.map((c) => `${c.external_id} — ${c.title} (${c.attached})`).join("\n"),
      });
      // Land on the work just made: the sprint's plan if there is one, else the first initiative's.
      const sprintIndex = planned.findIndex((p) => p.kind === "sprint");
      const landing = created[sprintIndex >= 0 ? sprintIndex : 0];
      if (landing) navigateToInitiative(landing.id);
      else navigateToPage("initiatives");
    },
    onError: (error) => {
      // Someone moved the work since this draft was drawn: redraw it, keeping the edits that still apply.
      if (error instanceof ApiError && error.status === 409) { // ts-org: allow-instanceof — narrowing to read the status, not formatting a message
        setAgentResult(null);
        void draft.refetch();
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["initiatives"] });
      void qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      void qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });

  const busy = create.isPending || regroup.isPending;
  const regroupError = regroup.isError && !isAbort(regroup.error) ? regroup.error : null;

  return (
    <div className="initiatives-page">
      <PageTopbar title="Suggested initiatives">
        <Button variant="secondary" onClick={() => navigateToPage("initiatives")}>
          Back to initiatives
        </Button>
      </PageTopbar>

      <div className="initiative-suggest-controls">
        <span className="initiative-toggle">
          <label htmlFor="suggest-sprint-length">Sprint length</label>
          <Select
            id="suggest-sprint-length"
            className="btn-secondary filter-select"
            value={weeks}
            disabled={busy}
            onChange={(e) => {
              setWeeks(Number(e.target.value));
              setAgentResult(null);
            }}
          >
            {SPRINT_WEEKS.map((w) => (
              <option key={w} value={w}>
                {w === 1 ? "1 week" : `${w} weeks`}
              </option>
            ))}
          </Select>
        </span>
        {agentResult && !regroup.isPending ? (
          <Button variant="secondary" disabled={busy} onClick={() => setAgentResult(null)}>
            Back to keyword draft
          </Button>
        ) : null}
        {regroup.isPending ? (
          <Button variant="secondary" onClick={() => controller.current?.abort()}>
            Stop waiting ({elapsed}s)
          </Button>
        ) : (
          <Button variant="secondary" disabled={busy || !draft.data} onClick={() => regroup.mutate()}>
            {agentResult ? "Ask the agent again" : "Ask the agent to regroup"}
          </Button>
        )}
      </div>

      {regroup.isPending ? (
        <p className="initiative-hint" role="status">
          The agent is reading every open milestone and sprint candidate — usually a minute or two. You can keep editing
          the draft below; leaving this page stops the wait.
        </p>
      ) : regroupError ? (
        <div className="initiative-suggest-alert" role="alert">
          <strong>The agent could not regroup this work.</strong>
          <p>{describeError(regroupError)}</p>
        </div>
      ) : stopped ? (
        <p className="initiative-hint" role="status">
          Stopped waiting. The agent’s turn may still finish on the server; its answer is not kept.
        </p>
      ) : shown ? (
        <p className="initiative-hint">
          {shown.source === "agent"
            ? "The agent’s grouping, ticked as it proposed. Edit anything; nothing is written until you confirm."
            : "A keyword draft: milestones grouped by a word their titles share, and a sprint sized to recent pace. Themes start unticked — read them first, or ask the agent for a better grouping."}
        </p>
      ) : null}

      {shown && shown.warnings.length > 0 && (
        <div className="initiative-hint initiative-warn" role="status">
          <p>Some of what the agent proposed could not be used:</p>
          <ul>
            {shown.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {draft.isPending ? (
        <div aria-busy="true" aria-label="Loading suggestions">
          <div className="initiative-skeleton" />
          <div className="initiative-skeleton" />
        </div>
      ) : draft.isError && !shown ? (
        <div className="queue-page-empty" role="alert">
          <div>
            <p>Suggestions could not be drawn up: {describeError(draft.error)}</p>
            <Button variant="secondary" onClick={() => void draft.refetch()}>
              Retry
            </Button>
          </div>
        </div>
      ) : shown && review && shown.suggestions.length === 0 && shown.ungrouped.length === 0 ? (
        <>
          <p className="initiative-intro">
            Nothing to suggest. Every open milestone already belongs to an initiative and no open feature or bug is
            waiting for a sprint. New milestones and work show up here as soon as they exist.
          </p>
          <NoSprintNote data={shown} />
        </>
      ) : shown && review ? (
        <>
          {shown.suggestions.length === 0 && (
            <p className="initiative-intro">
              No theme found among the {shown.ungrouped.length} open milestones without an initiative, and no sprint to
              plan. Start an initiative from any of them below.
            </p>
          )}
          <NoSprintNote data={shown} />
          <SuggestionReview
            data={shown}
            review={review}
            creating={create.isPending}
            onChange={(next) => setReviews((current) => ({ ...current, [shown.source]: next }))}
            onCreate={() => create.mutate()}
          />
        </>
      ) : null}
    </div>
  );
}
