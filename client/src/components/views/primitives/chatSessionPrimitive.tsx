/**
 * A Baxter conversation, as a pane.
 *
 * The thirteen `chat_*` primitives are chat *cards* — a ticket, a board, a
 * commit — lifted out of a thread. This is the thread itself: the messages, and
 * a composer that sends into them.
 *
 * ## Why it could not exist before
 *
 * `useBaxterChatSession` read the open thread's id from `uiStore`, one id for
 * the whole app. Two panes would have shared a conversation and each would have
 * switched the other's, and a container primitive may not import `state/` at
 * all — a zustand read outside a provider returns a value rather than throwing,
 * so the coupling would not show up as a failure. `useBaxterChatSessionAt`
 * takes the id instead, and here the id is a *setting*, which is what makes two
 * of these panes mean two conversations.
 *
 * ## The pane does not switch threads
 *
 * `openSession` is a no-op here, deliberately. A switcher inside the pane would
 * change what it shows without changing what it *is*, so the pane would open on
 * the old thread after a reload — a silent loss of the operator's choice. Which
 * conversation a pane holds is its settings, where the change is stored.
 *
 * ## Workspace as a setting, like the terminal
 *
 * Not read from the sidebar. A conversation belongs to a workspace, a view can
 * hold panes about more than one, and the terminal primitive already
 * established that a pane naming its own workspace is the honest shape.
 */

import { useState } from "react";

import { useBaxterChatSessionAt } from "../../../hooks/useBaxterChatSession";
import { StudioChatComposer, StudioChatMessages } from "../../studio/StudioChat";
import { usePaneSize } from "../paneSize";
import { definePrimitive } from "./definePrimitive";
import { Unconfigured } from "./Unconfigured";
import "./chatSession.css";

type ChatSessionSettings = {
  workspaceSlug: string;
  sessionId: string;
};

function ChatSessionPane({ workspaceSlug, sessionId }: ChatSessionSettings) {
  // The pane is pinned to its setting; see the note above. `openSession` still
  // has to be *something*, and a no-op is the honest one.
  const chat = useBaxterChatSessionAt(workspaceSlug, sessionId, () => {});
  const [draft, setDraft] = useState("");
  // A short pane is otherwise mostly composer: the page-sized box took 110px of
  // a 179px pane. `dense` is the composer's own compact size rather than this
  // stylesheet reaching into its internals.
  const { tier } = usePaneSize();

  // No "is it empty, is it busy" guard here: `StudioChatComposer` computes
  // exactly that as `canSend` and does not call `onSubmit` unless it holds. A
  // second copy is one that can drift from the control enforcing it — and a
  // mutation removing the copy changed nothing, which is how it was found.
  const submit = () => {
    const text = draft.trim();
    setDraft("");
    void chat.send(text);
  };

  if (chat.loadError) {
    return <Unconfigured>This conversation could not be loaded.</Unconfigured>;
  }

  return (
    <div className="chat-session-pane">
      <div className="chat-session-thread">
        <StudioChatMessages
          messages={chat.messages}
          isThinking={chat.isBusy}
          activeTurnId={chat.activeTurnId}
          assistantLabel="Baxter"
          showAssistantAvatar={false}
          thinkingMessage="Baxter is looking…"
          thinkingSub="Fetching a reply from your workspace model"
          thinkingActivity="typing"
          emptyMessage="Nothing in this conversation yet."
          onPrimitiveSubmit={(content) => void chat.send(content)}
        />
      </div>
      <StudioChatComposer
        value={draft}
        onChange={setDraft}
        onSubmit={submit}
        onStop={() => void chat.stop().catch(() => undefined)}
        placeholder="Reply to Baxter…"
        sendLabel="Send"
        isSending={chat.isBusy}
        isStopping={chat.isStopping}
        variant="dock"
        dense={tier === "compact"}
        error={chat.error ?? undefined}
      />
    </div>
  );
}

export const chatSessionPrimitive = definePrimitive<ChatSessionSettings>({
  id: "chat_session",
  displayName: "Conversation",
  icon: "✦",
  category: "Chat",
  containerKind: "panel",
  settingsFields: [
    {
      key: "workspace_slug",
      kind: "choice",
      source: "workspace",
      label: "Workspace",
      default: "",
      help: "The workspace this conversation belongs to.",
    },
    {
      key: "session_id",
      kind: "choice",
      source: "chat_session",
      label: "Conversation",
      default: "",
      help: "Which thread this pane shows. Start one from Home or the chat page.",
    },
  ],
  parseSettings: (raw) => ({
    workspaceSlug: typeof raw.workspace_slug === "string" ? raw.workspace_slug : "",
    sessionId: typeof raw.session_id === "string" ? raw.session_id : "",
  }),
  Component: ({ settings }) => {
    // Both, not either: the snapshot is fetched by the pair, and a thread id
    // without the workspace that owns it is a request that cannot be formed.
    if (settings.workspaceSlug === "" || settings.sessionId === "") {
      return <Unconfigured>This pane has no conversation yet.</Unconfigured>;
    }
    return <ChatSessionPane {...settings} />;
  },
});
