import { TRIAGE_AGENT_NAME } from "../../lib/triageAgent";
import { StudioChatComposer, StudioChatMessages } from "./StudioChat";

export function TicketStudioChatMessages({
  messages,
  emptyMessage,
  isThinking,
  activeTurnId,
  thinkingMessage = `${TRIAGE_AGENT_NAME} is thinking…`,
}: {
  messages: Parameters<typeof StudioChatMessages>[0]["messages"];
  emptyMessage?: string;
  isThinking?: boolean;
  /** The scoper turn in flight, so its reasoning streams into the busy state. */
  activeTurnId?: string | null;
  thinkingMessage?: string;
}) {
  return (
    <StudioChatMessages
      messages={messages}
      emptyMessage={emptyMessage}
      isThinking={isThinking}
      activeTurnId={activeTurnId}
      thinkingMessage={thinkingMessage}
      thinkingActivity="thinking"
      assistantLabel={TRIAGE_AGENT_NAME}
    />
  );
}

export function TicketStudioComposer({
  value,
  onChange,
  onSubmit,
  placeholder,
  isSending,
  onStop,
  isStopping,
  disabled,
  modelLabel,
  onModelClick,
  modelDisabled,
  onReviewBrief,
  onGenerateTickets,
  reviewPending,
  generatePending,
  generateDisabled,
  generateTitle,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  isSending?: boolean;
  /** Settle the in-flight turn. Without it the composer offers no way out. */
  onStop?: () => void;
  isStopping?: boolean;
  /**
   * The toolbar actions are unavailable.
   *
   * Deliberately not forwarded to the composer itself: `StudioChatComposer`
   * gates its Stop control on `!disabled`, so passing the busy state here
   * disabled the one control that exists for the busy state. Sending is already
   * held off by `isSending`.
   */
  disabled?: boolean;
  modelLabel: string;
  onModelClick: () => void;
  modelDisabled?: boolean;
  onReviewBrief: () => void;
  onGenerateTickets: () => void;
  reviewPending?: boolean;
  generatePending?: boolean;
  generateDisabled?: boolean;
  generateTitle?: string;
}) {
  return (
    <StudioChatComposer
      value={value}
      onChange={onChange}
      onSubmit={onSubmit}
      placeholder={placeholder}
      isSending={isSending}
      onStop={onStop}
      isStopping={isStopping}
      toolbar={
        <>
          <button
            type="button"
            className="ticket-studio-composer-action"
            disabled={disabled || modelDisabled}
            onClick={onModelClick}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <path d="M12 6v6l4 2" />
            </svg>
            {modelLabel}
          </button>
          <button
            type="button"
            className="ticket-studio-composer-action"
            disabled={disabled || reviewPending}
            onClick={onReviewBrief}
          >
            {reviewPending ? "Reviewing…" : "Review brief"}
          </button>
          <button
            type="button"
            className="ticket-studio-composer-action accent"
            disabled={disabled || generateDisabled || generatePending}
            onClick={onGenerateTickets}
            title={generateTitle}
          >
            {generatePending ? "Generating…" : "Generate tickets"}
          </button>
        </>
      }
    />
  );
}
