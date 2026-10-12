import type { InitiativeView } from "../../api/client";
import { parseChatScope } from "../../lib/chatScope";
import { navigateToInitiative } from "../../lib/useAppNavigation";
import { Button } from "../ui/Button";

/**
 * What a global chat is answering about, under the greeting.
 *
 * A workspace chat needs no note: the topbar picker names the workspace, and
 * that is the whole of its scope. A global chat is easy to mistake for one, so
 * it says it spans every workspace, which checkout its turns run in once the
 * thread exists, and, when focused, which initiative, with the way to its plan.
 */
export function ChatScopeNote({
  scope,
  initiatives,
  hostSlug,
}: {
  scope: string;
  initiatives: readonly InitiativeView[];
  /** The thread's host workspace; null until the first message creates the thread. */
  hostSlug: string | null;
}) {
  const parsed = parseChatScope(scope);
  if (parsed?.kind !== "global") return null;
  const { initiativeId } = parsed;
  const initiative = initiativeId
    ? initiatives.find((item) => item.id === initiativeId)
    : undefined;
  const runsIn = hostSlug ? ` Turns run in the ${hostSlug} checkout.` : "";

  return (
    <p className="baxter-chat-scope-note">
      {initiativeId ? (
        <>
          <span>
            {initiative
              ? `Working on ${initiative.external_id} · ${initiative.title}, across every workspace it touches.`
              : "Working on an initiative, across every workspace it touches."}
            {runsIn}
          </span>
          <Button variant="secondary" compact onClick={() => navigateToInitiative(initiativeId)}>
            Open plan
          </Button>
        </>
      ) : (
        <span>{`Answering across every workspace.${runsIn}`}</span>
      )}
    </p>
  );
}
