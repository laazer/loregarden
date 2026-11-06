/**
 * The dialogs a sidebar quick action opens over the current page.
 *
 * Each one is the owning page's own form component, so its fields, validation
 * and failure handling are the same as on that page; only where it opens, and
 * where the operator is left afterwards, differ.
 */

import type { SidebarQuickDialog } from "./appSidebarQuickActions";
import { ReferenceSourceForm } from "./memory/ReferenceSourceForm";

export function SidebarQuickDialogHost({
  dialog,
  workspaceSlug,
  onClose,
}: {
  dialog: SidebarQuickDialog | null;
  /** "" until a workspace is chosen; a dialog needs one to write against. */
  workspaceSlug: string;
  onClose: () => void;
}) {
  if (dialog === null || workspaceSlug === "") return null;
  return (
    <ReferenceSourceForm
      workspace={workspaceSlug}
      onCancel={onClose}
      // The form announces what it added; the operator stays on their page.
      onDone={onClose}
    />
  );
}
