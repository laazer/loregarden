import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { StudioAgentPreview } from "../../../api/types";
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

/** Lines of the role shown on the card. */
const ROLE_LINES = 8;

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
  // The role's opening, enough to know which agent this is; the rest is in Studio.
  const firstLines = (preview?.markdown ?? "")
    .split("\n")
    .filter((line) => line.trim())
    .slice(0, ROLE_LINES);
  const profile = preview?.profile;

  return (
    <PrimitiveCard
      kind="agent"
      title={part.title ?? preview?.name ?? slug ?? "Agent"}
      refLine={slug}
      copyRef={slug}
      loading={isLoading && !part.draft}
      error={
        !part.draft && error
          ? error instanceof Error
            ? error.message
            : "Failed to load agent"
          : null
      }
      meta={
        profile ? (
          <>
            {profile.provider ? (
              <span className="lg-primitive-fact">
                Provider <b>{profile.provider}</b>
              </span>
            ) : null}
            {profile.model ? (
              <span className="lg-primitive-fact">
                Model <b>{profile.model}</b>
              </span>
            ) : null}
            <span className="lg-primitive-fact">
              Timeout <b>{profile.timeout}s</b>
            </span>
          </>
        ) : null
      }
      footerHint={part.draft ? "A draft, not saved to Agent Studio" : "Role file, first lines"}
      actions={slug && !part.draft ? <OpenAgentStudioButton slug={slug} /> : null}
    >
      {firstLines.length ? (
        <div className="lg-primitive-terminal">
          <pre className="lg-primitive-terminal-body">{firstLines.join("\n")}</pre>
        </div>
      ) : (
        <p className="lg-primitive-card-sub">This agent has no role text yet.</p>
      )}
    </PrimitiveCard>
  );
}
