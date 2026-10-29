/**
 * Agent Studio → New agent…: the shared create dialog, with an agent's words.
 * A draft carries its role, runtime and tools into the create, so the editor
 * opens on the drafted agent rather than a blank one.
 */

import { api } from "../../api/client";
import type { StudioGeneratedAgent } from "../../api/types";
import { StudioCreateDialog, type StudioCreateSpec } from "./StudioCreateDialog";

const SPEC: StudioCreateSpec<StudioGeneratedAgent> = {
  noun: "agent",
  effect: "Creates the agent, then opens its editor for its role, runtime and tools.",
  describePlaceholder: "Its role, its limits, when to use it",
  draftNote: "Drafted. Its role and tools go in with it; change anything below first.",
  slugHint: "How workflows and runs name it. It cannot be changed later.",
  listKey: ["studio-agents"],
  generate: (brief) => api.generateStudioAgent(brief),
  create: (fields, draft) =>
    api.createStudioAgent({
      ...fields,
      ...(draft
        ? {
            role_body: draft.role_body,
            ...(draft.adapter ? { adapter: draft.adapter } : {}),
            default_skill: draft.default_skill,
            ...(draft.mcp_tools.length ? { mcp_tools: draft.mcp_tools, mcp_enabled: true } : {}),
          }
        : {}),
    }),
};

export function NewAgentDialog(props: { open: boolean; onClose: () => void; onCreated: (slug: string) => void }) {
  return <StudioCreateDialog {...props} spec={SPEC} />;
}
