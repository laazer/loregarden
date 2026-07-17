import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { BaxterAvatar, type BaxterAvatarState } from "../chat/BaxterAvatar";
import { MarkdownContent } from "../chat/MarkdownContent";
import { chatMessageBody, isUserChatRole, type ChatMessageView } from "../chat/chatUtils";

export type StudioAssistantActivity = "thinking" | "typing";

function latestAssistantMessageId(messages: ChatMessageView[]): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (!isUserChatRole(messages[i].role)) return messages[i].id;
  }
  return null;
}

function useRespondingFlash(messages: ChatMessageView[], isBusy: boolean): boolean {
  const latestId = latestAssistantMessageId(messages);
  const previousIdRef = useRef<string | null>(latestId);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    const previousId = previousIdRef.current;
    previousIdRef.current = latestId;

    if (isBusy || !latestId || latestId === previousId) return;

    setFlash(true);
    const timer = window.setTimeout(() => setFlash(false), 1600);
    return () => window.clearTimeout(timer);
  }, [isBusy, latestId]);

  useEffect(() => {
    if (isBusy) setFlash(false);
  }, [isBusy]);

  return flash && !isBusy;
}

export function StudioChatMessages({
  messages,
  emptyMessage,
  isThinking,
  thinkingMessage = "Assistant is thinking…",
  thinkingActivity = "thinking",
  assistantLabel = "Assistant",
  autoScroll = true,
  className,
}: {
  messages: ChatMessageView[];
  emptyMessage?: string;
  isThinking?: boolean;
  thinkingMessage?: string;
  /** Which busy animation to play while waiting for the assistant. */
  thinkingActivity?: StudioAssistantActivity;
  assistantLabel?: string;
  autoScroll?: boolean;
  className?: string;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const responding = useRespondingFlash(messages, Boolean(isThinking));
  const latestAssistantId = latestAssistantMessageId(messages);

  useEffect(() => {
    if (!autoScroll) return;
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [autoScroll, messages.length, isThinking]);

  const busyState: BaxterAvatarState = thinkingActivity === "typing" ? "typing" : "thinking";
  const activeState: BaxterAvatarState = isThinking ? busyState : responding ? "responding" : "idle";

  if (messages.length === 0 && !isThinking) {
    return (
      <div className={["ticket-studio-messages", className].filter(Boolean).join(" ")}>
        <p className="ticket-studio-messages-empty">{emptyMessage ?? "No messages yet."}</p>
        <div ref={bottomRef} className="ticket-studio-messages-tail" aria-hidden />
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

        const state =
          !isThinking && message.id === latestAssistantId ? activeState : "idle";
        return (
          <div key={message.id} className="ticket-studio-msg-row">
            <BaxterAvatar state={state} label={assistantLabel} />
            <div className="ticket-studio-msg ticket-studio-msg-assistant">
              <MarkdownContent content={body} className="ticket-studio-msg-body" />
            </div>
          </div>
        );
      })}
      {isThinking ? (
        <div className="ticket-studio-msg-row ticket-studio-thinking-row">
          <BaxterAvatar state={busyState} label={assistantLabel} />
          <p className="ticket-studio-thinking">{thinkingMessage}</p>
        </div>
      ) : null}
      <div ref={bottomRef} className="ticket-studio-messages-tail" aria-hidden />
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
