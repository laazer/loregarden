/**
 * The dialogs a sidebar quick action opens over the current page.
 *
 * Each one is the owning page's own dialog component, so its fields, validation
 * and failure handling are the same as on that page; only where it opens, and
 * where the operator is left afterwards, differ.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { useUsageSnapshot } from "../hooks/useUsageSnapshot";
import { studioAgentPath, studioWorkflowPath } from "../lib/appNavigation";
import { quickDialogNeedsWorkspace, type SidebarQuickDialog } from "./appSidebarQuickActions";
import { LocalInstancesModal } from "./LocalInstancesModal";
import { ReferenceSourceForm } from "./memory/ReferenceSourceForm";
import { NewAgentDialog } from "./studio/NewAgentDialog";
import { NewWorkflowDialog } from "./studio/NewWorkflowDialog";
import { UsageModal } from "./UsageModal";

/** Machine → Usage, reading the same snapshot the rail's near-limit flag does. */
function QuickUsageDialog({ onClose }: { onClose: () => void }) {
  const usage = useUsageSnapshot();
  return (
    <UsageModal
      open
      snapshot={usage.query.data}
      isLoading={usage.query.isFetching}
      error={usage.query.error}
      onClose={onClose}
      onRefresh={usage.refresh}
    />
  );
}

/** Studios → New Workflow; "Start from" lists the existing workflows. */
function QuickNewWorkflowDialog({ onClose, onNavigate }: { onClose: () => void; onNavigate: (to: string) => void }) {
  const workflows = useQuery({ queryKey: ["studio-workflows"], queryFn: api.studioWorkflows });
  return (
    <NewWorkflowDialog
      open
      onClose={onClose}
      onCreated={(slug) => {
        onClose();
        onNavigate(studioWorkflowPath(slug));
      }}
      workflows={workflows.data ?? []}
    />
  );
}

export function SidebarQuickDialogHost({
  dialog,
  workspaceSlug,
  onClose,
  onNavigate,
}: {
  dialog: SidebarQuickDialog | null;
  /** "" until a workspace is chosen; a workspace-scoped dialog needs one to write against. */
  workspaceSlug: string;
  onClose: () => void;
  /** Where a create dialog sends the operator: the new resource's editor. */
  onNavigate: (to: string) => void;
}) {
  if (dialog === null) return null;
  if (workspaceSlug === "" && quickDialogNeedsWorkspace(dialog)) return null;
  switch (dialog) {
    case "usage":
      return <QuickUsageDialog onClose={onClose} />;
    case "instances":
      return <LocalInstancesModal open onClose={onClose} />;
    case "new-agent":
      return (
        <NewAgentDialog
          open
          onClose={onClose}
          // Creating an agent opens its editor, as it does from Agent Studio.
          onCreated={(slug) => {
            onClose();
            onNavigate(studioAgentPath(slug));
          }}
        />
      );
    case "new-workflow":
      return <QuickNewWorkflowDialog onClose={onClose} onNavigate={onNavigate} />;
    case "add-reference":
      return (
        <ReferenceSourceForm
          workspace={workspaceSlug}
          onCancel={onClose}
          // The form announces what it added; the operator stays on their page.
          onDone={onClose}
        />
      );
  }
}
