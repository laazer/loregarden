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

function isFenceLine(line: string): boolean {
  return /^\s*(?:```|~~~)/.test(line);
}

/** Bullets, ordered items, headings and quotes — markdown that needs real newlines. */
function isStructuralLine(line: string): boolean {
  return /^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+|>\s?)/.test(line);
}

function isStructuralContinuation(line: string): boolean {
  return Boolean(line.trim()) && (isStructuralLine(line) || /^\s+\S/.test(line));
}

/**
 * Prepare agent prose for the markdown renderer.
 *
 * Single newlines inside a paragraph are hard breaks, because agents wrap prose
 * by line and markdown would otherwise reflow it into one run. Structural
 * markdown — lists, headings, quotes, tables, fenced code — must keep its real
 * newlines instead, or a list written straight under its lead-in line ("Steps:"
 * with no blank line between) renders as literal `- ` text rather than a list.
 */
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

    if (isFenceLine(line)) {
      flushParagraph();
      const fenced: string[] = [line];
      index += 1;
      // Everything up to and including the closing fence is verbatim — adding
      // the paragraph hard-break spaces here would put them in the code.
      while (index < lines.length) {
        fenced.push(lines[index]);
        const closed = isFenceLine(lines[index]);
        index += 1;
        if (closed) break;
      }
      blocks.push(fenced.join("\n"));
      continue;
    }

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

    if (isStructuralLine(line)) {
      flushParagraph();
      const group: string[] = [];
      while (
        index < lines.length &&
        !isTableLine(lines[index]) &&
        !isFenceLine(lines[index]) &&
        isStructuralContinuation(lines[index])
      ) {
        group.push(lines[index]);
        index += 1;
      }
      blocks.push(group.join("\n"));
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
