import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { StudioAgentPreview } from "../../../api/types";
import { AgentPreviewContent } from "../../studio/AgentPreviewContent";
import type { AgentPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenAgentStudioButton } from "./ResourceActionButton";

function draftPreview(part: AgentPart): StudioAgentPreview {
  const draft = part.draft ?? {};
  return {
    name: String(draft.name ?? part.title ?? part.slug ?? part.agent_id ?? "Agent"),
    markdown: String(draft.role_body ?? draft.prompt ?? ""),
    sections: ["header", "role"],
    profile: {
      description: String(draft.description ?? ""),
      model: String(draft.model ?? ""),
      provider: String(draft.adapter ?? draft.provider ?? ""),
      default_skill: String(draft.default_skill ?? ""),
      timeout: Number(draft.timeout ?? 600),
      always_apply: null,
    },
  };
}

export function AgentPrimitive({ part }: { part: AgentPart }) {
  const slug = part.slug ?? part.agent_id ?? undefined;
  const { data, isLoading, error } = useQuery({
    queryKey: ["studio-agent", slug],
    queryFn: async () => {
      const agent = await api.studioAgent(slug!);
      return api.previewStudioAgent(agent);
    },
    enabled: Boolean(slug) && !part.draft,
  });

  const preview = part.draft ? draftPreview(part) : data;

  return (
    <PrimitiveCard
      title={part.title ?? preview?.name ?? slug ?? "Agent"}
      subtitle={slug ?? undefined}
      loading={isLoading && !part.draft}
      error={
        !part.draft && error
          ? error instanceof Error
            ? error.message
            : "Failed to load agent"
          : null
      }
      resourceAction={slug && !part.draft ? <OpenAgentStudioButton slug={slug} /> : null}
    >
      <AgentPreviewContent preview={preview} loading={false} slug={slug} compact showMeta={false} />
    </PrimitiveCard>
  );
}
