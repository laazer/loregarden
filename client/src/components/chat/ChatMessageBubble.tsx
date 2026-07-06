import { chatMessageBody, chatRoleLabel, formatChatTime, isUserChatRole, type ChatMessageView } from "./chatUtils";
import { MarkdownContent } from "./MarkdownContent";

export function ChatMessageBubble({
  message,
  assistantLabel = "Assistant",
}: {
  message: ChatMessageView;
  assistantLabel?: string;
}) {
  const isUser = isUserChatRole(message.role);
  const timestamp = formatChatTime(message.created_at);

  return (
    <div className={`chat-message ${isUser ? "chat-message-user" : "chat-message-assistant"}`}>
      <div className="chat-message-meta">
        {chatRoleLabel(message.role, assistantLabel)}
        {timestamp ? ` · ${timestamp}` : ""}
      </div>
      <MarkdownContent content={chatMessageBody(message)} className="chat-message-body" />
    </div>
  );
}
