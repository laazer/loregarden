import type { ReactNode } from "react";

import type { StageStatus, TicketState, WorkflowStageView } from "../../../api/types";
import {
  priorityStyle,
  stageStatusColor,
  stageStatusLabel,
  ticketStateColor,
  ticketStateLabel,
} from "../../../lib/ticketStates";

export type ProgressSegment = {
  color: string;
  active?: boolean;
};

/** Stage segments: done go quiet, the live index glows, the rest wait. */
export function stageProgressSegments(
  stages: WorkflowStageView[] | undefined,
): { segments: ProgressSegment[]; done: number; total: number } {
  const list = stages ?? [];
  const focus = list.findIndex((s) => s.status !== "done" && s.status !== "wont_do");
  const focusIndex = focus === -1 ? list.length - 1 : focus;
  return {
    segments: list.map((stage, index) => {
      const live = index === focusIndex;
      return {
        color: live
          ? stageStatusColor(stage.status)
          : stage.status === "done" || stage.status === "wont_do"
            ? "var(--bd2)"
            : "var(--bd)",
        active: live && (stage.status === "running" || stage.status === "awaiting"),
      };
    }),
    done: list.filter((s) => s.status === "done" || s.status === "wont_do").length,
    total: list.length,
  };
}

/** Child segments for a parent card — one dash per child, labelled done/total. */
export function childProgressSegments(
  children: { state: TicketState }[] | undefined,
): { segments: ProgressSegment[]; done: number; total: number } {
  const list = children ?? [];
  return {
    segments: list.map((child) => {
      const active = child.state === "in_progress" || child.state === "blocked";
      return {
        color: active
          ? ticketStateColor(child.state)
          : child.state === "done" || child.state === "wont_do"
            ? "var(--bd2)"
            : "var(--bd)",
        active: child.state === "in_progress",
      };
    }),
    done: list.filter((c) => c.state === "done" || c.state === "wont_do").length,
    total: list.length,
  };
}

/** The v6 console ticket card body: P-badge, state, segmented progress, stage line. */
export function TicketCardBody({
  title,
  externalId,
  priority,
  state,
  workspaceSlug,
  stageName,
  stageStatus,
  segments,
  progressLabel,
  compact = false,
  underPriority,
}: {
  title: string;
  /** Optional id shown ahead of the title (ticket lists). */
  externalId?: string;
  priority: number;
  state: TicketState;
  workspaceSlug?: string;
  stageName?: string;
  stageStatus?: StageStatus;
  segments: ProgressSegment[];
  /** Right-side fraction — stages for a leaf, children for a parent. */
  progressLabel?: string | null;
  /** Board tiles are too narrow for the badge-aligned indent. */
  compact?: boolean;
  /** Stacked under the P-badge — expand chevron on parent tree rows. */
  underPriority?: ReactNode;
}) {
  const prio = priorityStyle(priority);
  const stateColor = ticketStateColor(state);
  const stageColor = stageStatusColor(stageStatus);

  return (
    <div
      className={
        compact
          ? "lg-primitive-ticket-v6 lg-primitive-ticket-v6--compact"
          : "lg-primitive-ticket-v6"
      }
    >
      <div className="lg-primitive-ticket-v6-title-row">
        <div className="lg-primitive-ticket-prio-col">
          <span
            className="lg-primitive-ticket-prio"
            style={{
              color: prio.color,
              background: prio.background,
              borderColor: prio.border,
            }}
          >
            {prio.code}
          </span>
          {underPriority}
        </div>
        <h3 className="lg-primitive-ticket-v6-title">
          {externalId ? (
            <>
              <span className="lg-primitive-ticket-external-id">{externalId}</span>
              <span className="lg-primitive-ticket-title-sep" aria-hidden>
                {" "}
                ·{" "}
              </span>
            </>
          ) : null}
          {title}
        </h3>
      </div>

      <div className="lg-primitive-ticket-v6-meta">
        <span className="lg-primitive-ticket-state" style={{ color: stateColor }}>
          <span
            className="lg-primitive-ticket-state-dot"
            style={{ background: stateColor }}
            aria-hidden
          />
          {ticketStateLabel(state)}
        </span>
        {workspaceSlug ? (
          <>
            <span className="lg-primitive-ticket-sep" aria-hidden>
              ·
            </span>
            <span className="lg-primitive-ticket-slug">{workspaceSlug}</span>
          </>
        ) : null}
      </div>

      {segments.length ? (
        <div
          className="lg-primitive-ticket-segs"
          aria-label={progressLabel ? `Progress ${progressLabel}` : "Workflow progress"}
        >
          <div className="lg-primitive-ticket-segs-track">
            {segments.map((seg, index) => (
              <span
                key={index}
                className={
                  seg.active
                    ? "lg-primitive-ticket-seg lg-primitive-ticket-seg--active"
                    : "lg-primitive-ticket-seg"
                }
                style={{ background: seg.color }}
              />
            ))}
          </div>
          {progressLabel ? (
            <span className="lg-primitive-ticket-segs-label">{progressLabel}</span>
          ) : null}
        </div>
      ) : null}

      {stageName ? (
        <div className="lg-primitive-ticket-stage">
          <span
            className="lg-primitive-ticket-stage-dot"
            style={{ background: stageColor }}
            aria-hidden
          />
          <span className="lg-primitive-ticket-stage-name" style={{ color: stageColor }}>
            {stageName}
          </span>
          <span className="lg-primitive-ticket-stage-status">
            {stageStatusLabel(stageStatus)}
          </span>
        </div>
      ) : null}
    </div>
  );
}
