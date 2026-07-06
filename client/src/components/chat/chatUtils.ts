export interface ChatMessageView {
  id: string;
  role: string;
  content: string;
  created_at?: string;
  display_content?: string;
}

export function formatChatTime(iso?: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function chatRoleLabel(role: string, assistantLabel = "Assistant"): string {
  return role === "user" ? "You" : assistantLabel;
}

export function chatMessageBody(message: ChatMessageView): string {
  return message.display_content ?? message.content;
}

export function normalizeChatMarkdown(text: string): string {
  return text
    .replace(/\r\n/g, "\n")
    .split(/\n{2,}/)
    .map((block) => block.replace(/\n/g, "  \n"))
    .join("\n\n");
}

export function isUserChatRole(role: string): boolean {
  return role === "user";
}
