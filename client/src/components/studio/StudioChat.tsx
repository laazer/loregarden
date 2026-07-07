import type { ReactNode } from "react";
import { useEffect, useRef } from "react";

import { MarkdownContent } from "../chat/MarkdownContent";
import { chatMessageBody, isUserChatRole, type ChatMessageView } from "../chat/chatUtils";

export function StudioScoperAvatar() {
  return (
    <span className="studio-assistant-avatar studio-assistant-avatar--scoper" aria-hidden>
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#04140f" strokeWidth="2.4">
        <path d="M12 3v18M5 8l7-5 7 5" />
      </svg>
    </span>
  );
}

export function StudioTriageAvatar() {
  return (
    <span className="studio-assistant-avatar studio-assistant-avatar--triage" aria-hidden>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#0f1530" strokeWidth="2.2">
        <path d="M12 3a7 7 0 0 1 7 7v2h1a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2h1v-2a7 7 0 0 1 7-7z" />
        <circle cx="9" cy="13" r="1" fill="#0f1530" />
        <circle cx="15" cy="13" r="1" fill="#0f1530" />
      </svg>
    </span>
  );
}

export function StudioChatMessages({
  messages,
  emptyMessage,
  isThinking,
  thinkingMessage = "Assistant is thinking…",
  assistantAvatar = <StudioScoperAvatar />,
  autoScroll = true,
  className,
}: {
  messages: ChatMessageView[];
  emptyMessage?: string;
  isThinking?: boolean;
  thinkingMessage?: string;
  assistantAvatar?: ReactNode;
  autoScroll?: boolean;
  className?: string;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!autoScroll) return;
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [autoScroll, messages.length, isThinking]);

  if (messages.length === 0 && !isThinking) {
    return (
      <div className={["ticket-studio-messages", className].filter(Boolean).join(" ")}>
        <p className="ticket-studio-messages-empty">{emptyMessage ?? "No messages yet."}</p>
        <div ref={bottomRef} />
      </div>
    );
  }

  return (
    <div className={["ticket-studio-messages", className].filter(Boolean).join(" ")}>
      {messages.map((message) => {
        const isUser = isUserChatRole(message.role);
        const body = chatMessageBody(message);

        if (isUser) {
          return (
            <div key={message.id} className="ticket-studio-msg ticket-studio-msg-user">
              <MarkdownContent content={body} className="ticket-studio-msg-body" />
            </div>
          );
        }

        return (
          <div key={message.id} className="ticket-studio-msg-row">
            {assistantAvatar}
            <div className="ticket-studio-msg ticket-studio-msg-assistant">
              <MarkdownContent content={body} className="ticket-studio-msg-body" />
            </div>
          </div>
        );
      })}
      {isThinking ? <p className="ticket-studio-thinking">{thinkingMessage}</p> : null}
      <div ref={bottomRef} />
    </div>
  );
}

export function StudioChatComposer({
  value,
  onChange,
  onSubmit,
  placeholder,
  isSending,
  disabled,
  sendLabel = "Send",
  sendingLabel = "Sending…",
  toolbar,
  optionsRow,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  isSending?: boolean;
  disabled?: boolean;
  sendLabel?: string;
  sendingLabel?: string;
  toolbar?: ReactNode;
  optionsRow?: ReactNode;
  error?: string | null;
}) {
  const canSend = value.trim().length > 0 && !isSending && !disabled;

  const submit = () => {
    if (!canSend) return;
    onSubmit();
  };

  return (
    <div className="ticket-studio-composer-wrap">
      {optionsRow ? <div className="studio-chat-composer-options">{optionsRow}</div> : null}
      <div className="ticket-studio-composer">
        <textarea
          className="ticket-studio-composer-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled || isSending}
          rows={2}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <div className="ticket-studio-composer-toolbar">
          {toolbar}
          <div className="ticket-studio-composer-spacer" />
          <button type="button" className="ticket-studio-composer-send" disabled={!canSend} onClick={submit}>
            {isSending ? sendingLabel : sendLabel}
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
              <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
            </svg>
          </button>
        </div>
        {error ? <div className="studio-chat-composer-error">{error}</div> : null}
      </div>
    </div>
  );
}
