import type { ReactNode } from "react";
import { memo, useEffect, useRef, useState } from "react";

import { useChatTurnThinking } from "../../hooks/useChatTurnThinking";
import { BaxterAvatar, type BaxterAvatarState } from "../chat/BaxterAvatar";
import { LiveThinkingStream } from "../chat/LiveThinkingStream";
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
  activeTurnId,
  autoScroll = true,
  className,
  renderAfterMessage,
  trailingAsk,
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
  /**
   * The turn in flight, from the surface's snapshot (`active_turn_id`, or
   * `active_run_id` for ticket triage). Given one, the busy state shows the
   * agent's reasoning as it arrives instead of a fixed "working…" line.
   */
  activeTurnId?: string | null;
  autoScroll?: boolean;
  className?: string;
  renderAfterMessage?: (message: ChatMessageView) => ReactNode;
  /**
   * Live decisions waiting on the operator (AskUserQuestion, permissions).
   * Rendered as a trailing assistant turn inside the thread — not chrome
   * layered above it.
   */
  trailingAsk?: ReactNode;
  /** When false, assistant turns are bubble-only (Baxter main chat look). */
  showAssistantAvatar?: boolean;
  /** Lets interactive primitives, such as Q&A, send a user reply. */
  onPrimitiveSubmit?: (content: string) => void;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const responding = useRespondingFlash(messages, Boolean(isThinking));
  const latestAssistantId = latestAssistantMessageId(messages);
  // Only while the turn is actually in flight: a settled turn's id would keep
  // a socket open against a channel that has already closed.
  const thinking = useChatTurnThinking(isThinking ? activeTurnId : null);
  // Activity alone is not enough to replace the placeholder: a header over an
  // empty box reads as broken. There has to be something to actually read.
  const hasLiveThinking = Boolean(thinking.content.trim() || thinking.answer.trim());
  const hasTrailingAsk = Boolean(trailingAsk);

  useEffect(() => {
    if (!autoScroll) return;
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [autoScroll, messages.length, isThinking, hasTrailingAsk]);

  const busyState: BaxterAvatarState = thinkingActivity === "typing" ? "typing" : "thinking";
  const activeState: BaxterAvatarState = isThinking ? busyState : responding ? "responding" : "idle";

  if (messages.length === 0 && !isThinking && !hasTrailingAsk) {
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
        // Reasoning reads before the conclusion it produced, so the turn's
        // thinking card leads rather than trailing the other primitives.
        const leadingParts = (parts ?? []).filter((p) => p.primitive === "thinking");
        const nonTextParts = (parts ?? []).filter(
          (p) => p.primitive !== "text" && p.primitive !== "thinking",
        );
        const hasNonTextParts = nonTextParts.length > 0;
        const partsSize = widestPrimitiveSize(nonTextParts);
        const leadingReasoning = leadingParts.length ? (
          <PrimitiveParts parts={leadingParts} onSubmit={onPrimitiveSubmit} />
        ) : null;
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
            {leadingReasoning}
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
                {leadingReasoning}
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
      {hasTrailingAsk ? (
        <div className="lg-chat-turn lg-chat-turn--assistant lg-chat-turn--ask">
          <div className="lg-chat-assistant-col">
            {showAssistantAvatar ? (
              <div className="lg-chat-assistant-row ticket-studio-msg-row">
                <BaxterAvatar variant="head" state="idle" label={assistantLabel} />
              </div>
            ) : null}
            {trailingAsk}
          </div>
        </div>
      ) : null}
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
            {hasLiveThinking ? (
              // The pacing walker is a stand-in for not knowing what the agent
              // is doing. Once it is telling us, showing both is noise.
              <>
                {thinking.content.trim() ? (
                  <LiveThinkingStream
                    content={thinking.content}
                    activity={thinking.activity}
                    label={assistantLabel}
                  />
                ) : null}
                {thinking.answer.trim() ? (
                  // The reply as it forms. Not marked up as markdown while it
                  // streams: half a fenced block or half a table renders as
                  // garbage that rearranges itself on every frame.
                  <div className="lg-chat-reply lg-chat-reply--streaming">
                    {thinking.answer}
                  </div>
                ) : null}
              </>
            ) : (
              <>
                <div className="lg-chat-loading-track" aria-hidden>
                  <div className="lg-chat-loading-walker">
                    <BaxterAvatar
                      variant="full"
                      state={busyState}
                      size={56}
                      label={assistantLabel}
                    />
                  </div>
                </div>
                <p className="lg-chat-loading-sub">{thinkingSub}</p>
              </>
            )}
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
  onStop,
  placeholder,
  isSending,
  isStopping,
  disabled,
  sendLabel = "Send",
  sendingLabel = "Sending…",
  stopLabel = "Stop",
  toolbar,
  error,
  variant = "panel",
  showShortcut,
  iconOnlySend,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  /** When set, the send control becomes Stop while a turn is in flight. */
  onStop?: () => void;
  placeholder?: string;
  isSending?: boolean;
  isStopping?: boolean;
  disabled?: boolean;
  sendLabel?: string;
  sendingLabel?: string;
  stopLabel?: string;
  toolbar?: ReactNode;
  error?: string | null;
  /** `panel` for side panes; `dock` for the floating bottom composer. */
  variant?: StudioChatComposerVariant;
  showShortcut?: boolean;
  /** Round icon send button (Baxter main chat). Default when variant is dock. */
  iconOnlySend?: boolean;
}) {
  const canStop = Boolean(isSending && onStop) && !isStopping && !disabled;
  const canSend = value.trim().length > 0 && !isSending && !disabled;
  const showStop = Boolean(isSending && onStop);
  // Round icon-only is fine for Send; Stop must read as Stop — a square swap
  // on the same accent chip is too easy to miss mid-stream.
  const roundSend = (iconOnlySend ?? variant === "dock") && !showStop;

  const submit = () => {
    if (!canSend) return;
    onSubmit();
  };

  const stop = () => {
    if (!canStop || !onStop) return;
    onStop();
  };

  return (
    <div
      className={[
        "lg-chat-composer-wrap",
        `lg-chat-composer-wrap--${variant}`,
        "ticket-studio-composer-wrap",
      ].join(" ")}
    >
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
              if (showStop) stop();
              else submit();
            }
          }}
        />
        <div className="lg-chat-composer-toolbar ticket-studio-composer-toolbar">
          {toolbar}
          <div className="lg-chat-composer-spacer ticket-studio-composer-spacer" />
          {showShortcut && !showStop ? (
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
              showStop ? "lg-chat-composer-send--stop" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            disabled={showStop ? !canStop : !canSend}
            onClick={showStop ? stop : submit}
            aria-label={
              roundSend
                ? isSending
                  ? sendingLabel
                  : sendLabel
                : showStop
                  ? isStopping
                    ? "Stopping…"
                    : stopLabel
                  : undefined
            }
          >
            {roundSend
              ? null
              : showStop
                ? isStopping
                  ? "Stopping…"
                  : stopLabel
                : isSending
                  ? sendingLabel
                  : sendLabel}
            {showStop ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                <rect x="6" y="6" width="12" height="12" rx="1.5" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
                <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
              </svg>
            )}
          </button>
        </div>
        {error ? <div className="lg-chat-composer-error studio-chat-composer-error">{error}</div> : null}
      </div>
    </div>
  );
}
