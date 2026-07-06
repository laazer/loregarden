import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";

import { normalizeChatMarkdown } from "./chatUtils";

const markdownComponents: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
};

export function MarkdownContent({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  const trimmed = content.trim();
  if (!trimmed) return null;

  return (
    <div className={["markdown-preview", className].filter(Boolean).join(" ")}>
      <ReactMarkdown components={markdownComponents}>{normalizeChatMarkdown(trimmed)}</ReactMarkdown>
    </div>
  );
}
