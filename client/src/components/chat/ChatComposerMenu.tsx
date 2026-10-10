import { AddToTabItems } from "../AddToTabMenu";
import { OverflowMenu, OverflowMenuItem, OverflowMenuSection } from "../OverflowMenu";

/**
 * The chat's `⋯`, in the composer rather than floating in the thread's margin.
 *
 * Everything that acts on the conversation as a whole lives here; the topbar
 * keeps History and New chat as well, because those are where people look first.
 */
export function ChatComposerMenu({
  workspaceSlug,
  sessionId,
  title,
  workbenchOpen,
  onToggleWorkbench,
  onOpenHistory,
  onNewChat,
  onOpenGallery,
}: {
  workspaceSlug: string;
  /** "" until the first message creates the thread; nothing to send to a tab yet. */
  sessionId: string;
  title: string;
  workbenchOpen: boolean;
  onToggleWorkbench: () => void;
  onOpenHistory: () => void;
  onNewChat: () => void;
  onOpenGallery: () => void;
}) {
  return (
    <OverflowMenu label="Chat actions">
      <OverflowMenuSection title="Chat" />
      <OverflowMenuItem onSelect={onNewChat}>New chat</OverflowMenuItem>
      <OverflowMenuItem onSelect={onOpenHistory}>History…</OverflowMenuItem>
      <OverflowMenuItem onSelect={onOpenGallery}>Primitive gallery</OverflowMenuItem>
      <OverflowMenuItem onSelect={onToggleWorkbench}>
        {workbenchOpen ? "Hide workbench" : "Show workbench"}
      </OverflowMenuItem>
      {sessionId ? (
        <AddToTabItems
          primitiveId="chat_session"
          values={
            new Map([
              ["workspace_slug", workspaceSlug],
              ["session_id", sessionId],
            ])
          }
          title={title || "Conversation"}
        />
      ) : null}
    </OverflowMenu>
  );
}
