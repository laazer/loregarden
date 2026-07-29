import type { ChatPart, UnknownPart } from "./primitives/types";

export interface ChatMessageView {
  id: string;
  role: string;
  content: string;
  created_at?: string;
  display_content?: string;
  parts?: Array<ChatPart | UnknownPart>;
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

function isTableLine(line: string): boolean {
  return line.trim().startsWith("|");
}

export function normalizeChatMarkdown(text: string): string {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: string[] = [];
  let paragraphLines: string[] = [];

  const flushParagraph = () => {
    if (paragraphLines.length === 0) return;
    blocks.push(paragraphLines.join("  \n"));
    paragraphLines = [];
  };

  let index = 0;
  while (index < lines.length) {
    const line = lines[index];

    if (isTableLine(line)) {
      flushParagraph();
      const tableLines: string[] = [];
      while (index < lines.length && isTableLine(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      blocks.push(tableLines.join("\n"));
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      index += 1;
      continue;
    }

    paragraphLines.push(line);
    index += 1;
  }

  flushParagraph();
  return blocks.join("\n\n");
}

export function isUserChatRole(role: string): boolean {
  return role === "user";
}

/** Prefer structured parts for display; fall back to stripping fences from content. */
export function assistantTextFromParts(
  parts: Array<ChatPart | UnknownPart> | undefined,
  fallback: string,
): string {
  if (!parts?.length) return fallback;
  const text = parts
    .filter((p): p is Extract<ChatPart, { primitive: "text" }> => p.primitive === "text")
    .map((p) => p.content)
    .join("\n\n")
    .trim();
  return text || fallback;
}
