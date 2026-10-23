/**
 * What the Studio's agent editor shows *about* an agent: the adapters it can
 * run on, and the category badge its slug earns.
 *
 * Lifted out of `StudioPage` for the same reason `studioWorkflowHelpers` was —
 * the merge of main into this tree put the page back over the 1200-line gate.
 * Neither of these reads any of the page's state, so they travel on their own.
 */

import type { StudioAgent } from "../../api/client";

/** The CLI adapters an agent may declare, as the editor's dropdown shows them. */
export const ADAPTERS = [
  { id: "claude", label: "Claude Code" },
  { id: "cursor", label: "Cursor Agent" },
  { id: "lmstudio", label: "LM Studio" },
  { id: "local", label: "Local runner" },
];

/** The badge an agent carries in the list: what kind of work its slug names. */
export function agentCategory(agent: StudioAgent): { label: string; className: string } {
  if (!agent.built_in) return { label: "Custom", className: "custom" };
  const slug = agent.slug.toLowerCase();
  if (slug.includes("plan")) return { label: "Planning", className: "planning" };
  if (slug.includes("review")) return { label: "Review", className: "review" };
  if (slug.includes("implement") || slug.includes("coder") || slug.includes("backend") || slug.includes("frontend")) {
    return { label: "Implementation", className: "implementation" };
  }
  if (slug.includes("test") || slug.includes("qa")) return { label: "Testing", className: "testing" };
  return { label: "Agent", className: "default" };
}
