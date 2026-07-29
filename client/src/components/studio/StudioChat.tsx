import type { ReactNode } from "react";
import { memo, useEffect, useRef, useState } from "react";

import { BaxterAvatar, type BaxterAvatarState } from "../chat/BaxterAvatar";
import { MarkdownContent } from "../chat/MarkdownContent";
import { PrimitiveParts } from "../chat/primitives/PrimitiveParts";
import { widestPrimitiveSize } from "../chat/primitives/primitiveFrame";
import type { ChatPart } from "../chat/primitives/types";
import { chatMessageBody, isUserChatRole, type ChatMessageView } from "../chat/chatUtils";
import "../chat/ChatLook.css";

export type StudioAssistantActivity = "thinking" | "typing";

export type StudioChatComposerVariant = "panel" | "dock";

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

export const StudioChatMessages = memo(function StudioChatMessages({
  messages,
  emptyMessage,
  isThinking,
  thinkingMessage = "Assistant is thinking…",
  thinkingSub = "Working on a reply…",
  thinkingActivity = "thinking",
  assistantLabel = "Assistant",
  autoScroll = true,
  className,
  renderAfterMessage,
  showAssistantAvatar = true,
  onPrimitiveSubmit,
}: {
  messages: ChatMessageView[];
  emptyMessage?: string;
  isThinking?: boolean;
  thinkingMessage?: string;
  thinkingSub?: string;
  /** Which busy animation to play while waiting for the assistant. */
  thinkingActivity?: StudioAssistantActivity;
  assistantLabel?: string;
  autoScroll?: boolean;
  className?: string;
  renderAfterMessage?: (message: ChatMessageView) => ReactNode;
  /** When false, assistant turns are bubble-only (Baxter main chat look). */
  showAssistantAvatar?: boolean;
  /** Lets interactive primitives, such as Q&A, send a user reply. */
  onPrimitiveSubmit?: (content: string) => void;
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
      <div className={["lg-chat-messages", "ticket-studio-messages", className].filter(Boolean).join(" ")}>
        <p className="lg-chat-messages-empty ticket-studio-messages-empty">
          {emptyMessage ?? "No messages yet."}
        </p>
        <div ref={bottomRef} className="lg-chat-messages-tail ticket-studio-messages-tail" aria-hidden />
      </div>
    );
  }

  return (
    <div className={["lg-chat-messages", "ticket-studio-messages", className].filter(Boolean).join(" ")}>
      {messages.map((message) => {
        const isUser = isUserChatRole(message.role);
        const body = chatMessageBody(message);
        const parts = message.parts;
        const nonTextParts = (parts ?? []).filter((p) => p.primitive !== "text");
        const hasNonTextParts = nonTextParts.length > 0;
        const partsSize = widestPrimitiveSize(nonTextParts);
        const textBody =
          parts?.length && hasNonTextParts
            ? parts
                .filter((p): p is Extract<ChatPart, { primitive: "text" }> => p.primitive === "text")
                .map((p) => p.content)
                .join("\n\n")
                .trim() || ""
            : body;

        if (isUser) {
          return (
            <div key={message.id} className="lg-chat-turn lg-chat-turn--user">
              <div className="lg-chat-user-bubble ticket-studio-msg ticket-studio-msg-user">
                <MarkdownContent content={body} className="ticket-studio-msg-body" />
              </div>
              {renderAfterMessage?.(message)}
            </div>
          );
        }

        const state =
          !isThinking && message.id === latestAssistantId ? activeState : "idle";
        const reply = (
          <>
            {textBody ? (
              <div className="lg-chat-reply ticket-studio-msg ticket-studio-msg-assistant">
                <MarkdownContent content={textBody} className="ticket-studio-msg-body" />
              </div>
            ) : null}
            {hasNonTextParts ? (
              <PrimitiveParts parts={nonTextParts} onSubmit={onPrimitiveSubmit} />
            ) : null}
          </>
        );
        return (
          <div
            key={message.id}
            className={[
              "lg-chat-turn",
              "lg-chat-turn--assistant",
              partsSize === "regular" ? null : `lg-chat-turn--${partsSize}`,
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {showAssistantAvatar ? (
              <div className="lg-chat-assistant-col">
                <div className="lg-chat-assistant-row ticket-studio-msg-row">
                  <BaxterAvatar variant="head" state={state} label={assistantLabel} />
                  {textBody ? (
                    <div className="lg-chat-reply ticket-studio-msg ticket-studio-msg-assistant">
                      <MarkdownContent content={textBody} className="ticket-studio-msg-body" />
                    </div>
                  ) : (
                    <div style={{ flex: 1 }} />
                  )}
                </div>
                {hasNonTextParts ? (
                  <PrimitiveParts parts={nonTextParts} onSubmit={onPrimitiveSubmit} />
                ) : null}
              </div>
            ) : (
              <div className="lg-chat-assistant-col">{reply}</div>
            )}
            {renderAfterMessage?.(message)}
          </div>
        );
      })}
      {isThinking ? (
        <div className="lg-chat-turn lg-chat-turn--assistant" role="status" aria-live="polite">
          <div className="lg-chat-loading">
            {showAssistantAvatar ? (
              <div className="lg-chat-loading-header ticket-studio-msg-row ticket-studio-thinking-row">
                <BaxterAvatar variant="head" state={busyState} label={assistantLabel} />
                <p className="lg-chat-loading-title ticket-studio-thinking">{thinkingMessage}</p>
              </div>
            ) : (
              <p className="lg-chat-loading-title">{thinkingMessage}</p>
            )}
            <div className="lg-chat-loading-track" aria-hidden>
              <div className="lg-chat-loading-walker">
                <BaxterAvatar variant="full" state={busyState} size={56} label={assistantLabel} />
              </div>
            </div>
            <p className="lg-chat-loading-sub">{thinkingSub}</p>
          </div>
        </div>
      ) : null}
      <div ref={bottomRef} className="lg-chat-messages-tail ticket-studio-messages-tail" aria-hidden />
    </div>
  );
});

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
  variant = "panel",
  showShortcut,
  iconOnlySend,
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
  /** `panel` for side panes; `dock` for the floating bottom composer. */
  variant?: StudioChatComposerVariant;
  showShortcut?: boolean;
  /** Round icon send button (Baxter main chat). Default when variant is dock. */
  iconOnlySend?: boolean;
}) {
  const canSend = value.trim().length > 0 && !isSending && !disabled;
  const roundSend = iconOnlySend ?? variant === "dock";

  const submit = () => {
    if (!canSend) return;
    onSubmit();
  };

  return (
    <div
      className={[
        "lg-chat-composer-wrap",
        `lg-chat-composer-wrap--${variant}`,
        "ticket-studio-composer-wrap",
      ].join(" ")}
    >
      {optionsRow ? <div className="studio-chat-composer-options">{optionsRow}</div> : null}
      <div className="lg-chat-composer ticket-studio-composer">
        <textarea
          className="lg-chat-composer-input ticket-studio-composer-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled || isSending}
          rows={variant === "dock" ? 1 : 2}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <div className="lg-chat-composer-toolbar ticket-studio-composer-toolbar">
          {toolbar}
          <div className="lg-chat-composer-spacer ticket-studio-composer-spacer" />
          {showShortcut ? (
            <span className="lg-chat-composer-shortcut" aria-hidden>
              ⌘J
            </span>
          ) : null}
          <button
            type="button"
            className={[
              "lg-chat-composer-send",
              "ticket-studio-composer-send",
              roundSend ? "" : "lg-chat-composer-send--labeled",
            ]
              .filter(Boolean)
              .join(" ")}
            disabled={!canSend}
            onClick={submit}
            aria-label={roundSend ? (isSending ? sendingLabel : sendLabel) : undefined}
          >
            {roundSend ? null : isSending ? sendingLabel : sendLabel}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
              <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
            </svg>
          </button>
        </div>
        {error ? <div className="lg-chat-composer-error studio-chat-composer-error">{error}</div> : null}
      </div>
    </div>
  );
}
