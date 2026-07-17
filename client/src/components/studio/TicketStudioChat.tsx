import { TRIAGE_AGENT_NAME } from "../../lib/triageAgent";
import { StudioChatComposer, StudioChatMessages } from "./StudioChat";

export function TicketStudioChatMessages({
  messages,
  emptyMessage,
  isThinking,
  thinkingMessage = `${TRIAGE_AGENT_NAME} is thinking…`,
}: {
  messages: Parameters<typeof StudioChatMessages>[0]["messages"];
  emptyMessage?: string;
  isThinking?: boolean;
  thinkingMessage?: string;
}) {
  return (
    <StudioChatMessages
      messages={messages}
      emptyMessage={emptyMessage}
      isThinking={isThinking}
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
      disabled={disabled}
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
