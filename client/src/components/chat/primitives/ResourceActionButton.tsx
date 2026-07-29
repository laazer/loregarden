import type { ReactNode } from "react";

import {
  navigateToPage,
  navigateToStudio,
  navigateToStudioAgent,
  navigateToStudioWorkflow,
  navigateToTicket,
  type ArtifactTab,
} from "../../../lib/useAppNavigation";
import { useUiStore } from "../../../state/uiStore";

function ArrowIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden
    >
      <path d="M7 17 17 7M8 7h9v9" />
    </svg>
  );
}

function ResourceActionButton({
  label,
  onClick,
  compact = false,
  children,
}: {
  label: string;
  onClick: () => void;
  compact?: boolean;
  children?: ReactNode;
}) {
  return (
    <button
      type="button"
      className={[
        "lg-primitive-resource-btn",
        compact ? "lg-primitive-resource-btn--compact" : null,
      ]
        .filter(Boolean)
        .join(" ")}
      aria-label={label}
      title={label}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
    >
      <span className="lg-primitive-resource-btn-label">{children ?? label}</span>
      <ArrowIcon />
    </button>
  );
}

export function OpenTicketButton({
  ticketId,
  label = "Open ticket",
  compact,
  tab = "diff",
}: {
  ticketId: string;
  label?: string;
  compact?: boolean;
  tab?: ArtifactTab;
}) {
  return (
    <ResourceActionButton
      label={label}
      compact={compact}
      onClick={() => navigateToTicket(ticketId, { tab })}
    />
  );
}

export function OpenAgentStudioButton({ slug }: { slug: string }) {
  return (
    <ResourceActionButton
      label="Open in Agent Studio"
      onClick={() => navigateToStudioAgent(slug)}
    />
  );
}

export function OpenWorkflowStudioButton({ slug }: { slug: string }) {
  return (
    <ResourceActionButton
      label="Open in Workflow Studio"
      onClick={() => navigateToStudioWorkflow(slug)}
    />
  );
}

export function OpenGateStudioButton() {
  return (
    <ResourceActionButton
      label="Open Gate Studio"
      onClick={() => navigateToStudio("gates")}
    />
  );
}

export function OpenIdeButton({ workspaceSlug }: { workspaceSlug?: string } = {}) {
  const setEditorWorkspace = useUiStore((state) => state.setEditorWorkspace);
  return (
    <ResourceActionButton
      label="Open IDE"
      onClick={() => {
        if (workspaceSlug) setEditorWorkspace(workspaceSlug);
        navigateToPage("editor");
      }}
    />
  );
}

/** Jump straight to a workspace file in the Editor page. */
export function OpenEditorFileButton({
  path,
  workspaceSlug,
  label,
  compact,
}: {
  path: string;
  workspaceSlug?: string;
  label?: string;
  compact?: boolean;
}) {
  const openEditorFile = useUiStore((state) => state.openEditorFile);
  const editorWorkspace = useUiStore((state) => state.editorWorkspace);
  const workspace = useUiStore((state) => state.workspace);
  const resolvedWorkspace =
    workspaceSlug ||
    editorWorkspace ||
    (workspace && workspace !== "all" ? workspace : "");
  const actionLabel = label ?? `Open ${path} in editor`;

  return (
    <ResourceActionButton
      label={actionLabel}
      compact={compact}
      onClick={() => {
        if (!resolvedWorkspace) {
          navigateToPage("editor");
          return;
        }
        openEditorFile(resolvedWorkspace, path);
      }}
    >
      Open in editor
    </ResourceActionButton>
  );
}

export function OpenBranchTriageButton({
  workspaceSlug,
  branch,
}: {
  workspaceSlug: string;
  branch: string;
}) {
  const setWorkspace = useUiStore((state) => state.setBranchTriageWorkspaceSlug);
  const setBranch = useUiStore((state) => state.setBranchTriageBranch);
  return (
    <ResourceActionButton
      label="Open branch triage"
      onClick={() => {
        setWorkspace(workspaceSlug);
        setBranch(branch);
        navigateToPage("branch-triage");
      }}
    />
  );
}
