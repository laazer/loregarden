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
            <span className="lg-primitive-fact">
              Tickets <b>{workspace.ticket_count}</b>
            </span>
            <span className="lg-primitive-fact">
              Workflow <b>{workspace.workflow_template_slug || "not configured"}</b>
            </span>
            <span className="lg-primitive-fact">
              Agent <b>{workspace.cli_adapter}</b>
            </span>
            <span className="lg-primitive-fact">
              Repo <b>{workspace.repo_exists ? "ready" : "unavailable"}</b>
            </span>
          </>
        ) : null
      }
      footerHint={
        workspace
          ? `Model: ${(workspace.cli_adapter === "cursor"
                ? workspace.cursor_model
                : workspace.claude_model || workspace.lmstudio_model) || "workspace default"}`
          : null
      }
      actions={<OpenIdeButton workspaceSlug={part.workspace_slug} />}
    />
  );
}
