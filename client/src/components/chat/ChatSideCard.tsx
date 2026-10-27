import type {
  ChatBranch,
  ChatFocus,
  FeatureLoop,
  FeatureLoopStartRequest,
} from "../../api/chatWorkbenchTypes";
import { useDialogDismiss } from "../../hooks/useDialogDismiss";
import { LivingMap } from "../livingMap/LivingMap";
import { Button } from "../ui/Button";
import { ChatPromptRow } from "./ChatPromptRow";
import { primitiveKindLabel, type PrimitiveHistoryEntry } from "./primitiveHistory";
import { ChangesCard } from "./workbench/ChangesCard";
import { FeatureCard } from "./workbench/FeatureCard";
import { ScopeCard } from "./workbench/ScopeCard";
import { WorkbenchCard } from "./workbench/WorkbenchCard";
import "./ChatSideCard.css";

/** The workbench's view of the thread: what each side card reads. */
export interface ChatWorkbenchState {
  focus: ChatFocus;
  isGlobal: boolean;
  /** The checkout turns run in; null until the thread exists. */
  hostSlug: string | null;
  feature: FeatureLoop | null;
  chatBranch: ChatBranch | null;
  /** Where a new feature ticket may live, and the one picked by default. */
  loopWorkspaces: string[];
  defaultLoopWorkspace: string;
  savingFocus: boolean;
  startingLoop: boolean;
  /** A criterion decision or add is in flight. */
  decidingCriteria: boolean;
  onSaveFocus: (body: { workspace_slugs: string[]; initiative_ids: string[] }) => Promise<unknown>;
  onStartLoop: (body: FeatureLoopStartRequest) => Promise<unknown>;
  onDecideCriteria: (ids: string[], accept: boolean) => void;
  onAddCriterion: (text: string) => Promise<unknown>;
}

/**
 * The chat page's workbench: a column of cards beside the thread.
 *
 * Scope says what the chat is about and where it runs; Feature holds the
 * feature loop's criteria and what waits on the operator; Changes & runs is
 * the branch and the loop's agent runs; Map is the conversation's living map
 * (what waits on you, what is open, what was settled); Suggestions is what to
 * ask next; and Cards indexes what the thread has produced, which was
 * otherwise only findable by scrolling back.
 *
 * The prompts live here only while the workbench is open; the page draws them
 * under the composer when it is closed.
 */
export function ChatSideCard({
  overlay,
  prompts,
  promptsDisabled,
  onPrompt,
  primitives,
  onJumpTo,
  onOpenGallery,
  onCollapse,
  workbench,
  sessionId,
}: {
  /** A narrow window: the workbench covers the thread, so Escape and the scrim close it. */
  overlay: boolean;
  prompts: readonly string[];
  /** A turn is in flight, or no workspace answers yet. */
  promptsDisabled: boolean;
  onPrompt: (text: string) => void;
  primitives: PrimitiveHistoryEntry[];
  onJumpTo: (messageId: string) => void;
  onOpenGallery: () => void;
  onCollapse: () => void;
  workbench: ChatWorkbenchState;
  /** The open chat, whose living map the Map card draws; "" before it has one. */
  sessionId: string;
}) {
  useDialogDismiss(overlay ? onCollapse : null);
  // Picking a card or a prompt closes the overlay, or what it leads to lands behind it.
  const jumpTo = (messageId: string) => {
    if (overlay) onCollapse();
    onJumpTo(messageId);
  };
  const pickPrompt = (text: string) => {
    if (overlay) onCollapse();
    onPrompt(text);
  };
  return (
    <>
      {overlay ? (
        <Button
          variant="plain"
          className="chat-side-card-scrim"
          aria-label="Close the workbench"
          tabIndex={-1}
          onClick={onCollapse}
        />
      ) : null}
      <aside className="chat-side-card" aria-label="Workbench">
        <header className="chat-side-card-head">
          <h2>Workbench</h2>
          <Button
            variant="plain"
            className="chat-side-card-icon-btn"
            aria-label="Hide the workbench"
            title="Hide the workbench"
            onClick={onCollapse}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="m9 18 6-6-6-6" />
            </svg>
          </Button>
        </header>

        <ScopeCard
          focus={workbench.focus}
          isGlobal={workbench.isGlobal}
          hostSlug={workbench.hostSlug}
          saving={workbench.savingFocus}
          onSave={workbench.onSaveFocus}
        />

        <FeatureCard
          feature={workbench.feature}
          workspaces={workbench.loopWorkspaces}
          defaultWorkspace={workbench.defaultLoopWorkspace}
          busy={workbench.decidingCriteria}
          starting={workbench.startingLoop}
          onStart={workbench.onStartLoop}
          onDecide={workbench.onDecideCriteria}
          onAdd={workbench.onAddCriterion}
        />

        <ChangesCard
          branch={workbench.chatBranch}
          runs={workbench.feature?.runs ?? []}
          hasLoop={workbench.feature !== null}
        />

        <WorkbenchCard title="Map">
          {sessionId ? (
            <LivingMap scope="chat" scopeRef={sessionId} />
          ) : (
            <p className="chat-side-card-empty">
              Start the conversation, and its plans, questions and topics are mapped here as they happen.
            </p>
          )}
        </WorkbenchCard>

        {prompts.length ? (
          <WorkbenchCard title="Suggestions">
            <ChatPromptRow
              prompts={prompts}
              disabled={promptsDisabled}
              onPick={pickPrompt}
              stacked
            />
          </WorkbenchCard>
        ) : null}

        <WorkbenchCard
          title="Cards"
          meta={primitives.length ? String(primitives.length) : undefined}
          action={
            <Button variant="plain" className="chat-side-card-link" onClick={onOpenGallery}>
              Gallery
            </Button>
          }
        >
          {primitives.length ? (
            // Newest first; each entry takes you to its card in the thread.
            <ol className="chat-side-history" aria-label={`${primitives.length} cards in this chat`}>
              {primitives.map((entry) => (
                <li key={entry.key}>
                  <Button
                    variant="plain"
                    className="chat-side-history-entry"
                    title="Show in thread"
                    onClick={() => jumpTo(entry.messageId)}
                  >
                    <span className="chat-side-history-kind">{primitiveKindLabel(entry.kind)}</span>
                    <span className="chat-side-history-label">{entry.label}</span>
                  </Button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="chat-side-card-empty">
              Tickets, plans, boards and diffs Baxter sends collect here, newest first. Open the
              gallery to see every kind.
            </p>
          )}
        </WorkbenchCard>
      </aside>
    </>
  );
}
