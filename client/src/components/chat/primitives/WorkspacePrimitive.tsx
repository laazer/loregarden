import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenIdeButton } from "./ResourceActionButton";
import type { WorkspacePart } from "./types";

export function WorkspacePrimitive({ part }: { part: WorkspacePart }) {
  const query = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.workspaces(),
  });
  const workspace = query.data?.find((item) => item.slug === part.workspace_slug);
  const missing = Boolean(query.data && !workspace);

  return (
    <PrimitiveCard
      kind="workspace"
      title={part.title ?? workspace?.name ?? part.workspace_slug}
      refLine={workspace?.repo_path ?? part.workspace_slug}
      copyRef={workspace?.repo_path}
      loading={query.isLoading}
      error={
        query.error
          ? query.error instanceof Error
            ? query.error.message
            : "Failed to load workspace"
          : missing
            ? "Workspace not found"
            : null
      }
      status={workspace?.blocked_count ? { tone: "bad", label: `${workspace.blocked_count} blocked` } : null}
      meta={
        workspace ? (
          <>
            <span>{workspace.ticket_count} tickets</span>
            <span>{workspace.cli_adapter}</span>
          </>
        ) : null
      }
      resourceAction={<OpenIdeButton workspaceSlug={part.workspace_slug} />}
    >
      {workspace ? (
        <div className="lg-primitive-workspace-grid">
          <div>
            <span className="lg-primitive-workspace-label">Workflow</span>
            <strong>{workspace.workflow_template_slug || "Not configured"}</strong>
          </div>
          <div>
            <span className="lg-primitive-workspace-label">Repository</span>
            <strong>{workspace.repo_exists ? "Ready" : "Unavailable"}</strong>
          </div>
          <div>
            <span className="lg-primitive-workspace-label">Model</span>
            <strong>
              {workspace.cli_adapter === "cursor"
                ? workspace.cursor_model
                : workspace.claude_model || workspace.lmstudio_model || "Default"}
            </strong>
          </div>
        </div>
      ) : null}
    </PrimitiveCard>
  );
}
