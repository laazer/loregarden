import { useEffect, useRef } from "react";

import { ChatMessageBubble } from "./ChatMessageBubble";
import type { ChatMessageView } from "./chatUtils";

export function ChatWindow({
  title,
  messages,
  emptyMessage,
  assistantLabel = "Assistant",
  isThinking = false,
  thinkingMessage = "Assistant is thinking…",
  autoScroll = true,
  minHeight = 220,
  maxHeight,
  flex = false,
  className,
}: {
  title?: string;
  messages: ChatMessageView[];
  emptyMessage?: string;
  assistantLabel?: string;
  isThinking?: boolean;
  thinkingMessage?: string;
  autoScroll?: boolean;
  minHeight?: number;
  maxHeight?: number | string;
  flex?: boolean;
  className?: string;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!autoScroll) return;
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [autoScroll, messages.length, isThinking]);

  const windowStyle = {
    minHeight,
    ...(maxHeight != null ? { maxHeight, overflow: "auto" as const } : {}),
    ...(flex ? { flex: 1, minHeight: 0 } : {}),
  };

  return (
    <section className={["chat-section", className].filter(Boolean).join(" ")}>
      {title ? <div className="state-label chat-section-title">{title}</div> : null}
      <div className="chat-window" style={windowStyle}>
        {messages.length === 0 && !isThinking ? (
          <div className="chat-empty">{emptyMessage ?? "No messages yet."}</div>
        ) : (
          messages.map((message) => (
            <ChatMessageBubble key={message.id} message={message} assistantLabel={assistantLabel} />
          ))
        )}
        {isThinking ? <div className="chat-thinking">{thinkingMessage}</div> : null}
        <div ref={bottomRef} />
      </div>
    </section>
  );
}
