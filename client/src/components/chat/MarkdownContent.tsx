import { memo } from "react";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { normalizeChatMarkdown } from "./chatUtils";

const markdownComponents: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="markdown-table-wrap">
      <table>{children}</table>
    </div>
  ),
};

export const MarkdownContent = memo(function MarkdownContent({
  content,
  className,
  normalize = true,
}: {
  content: string;
  className?: string;
  normalize?: boolean;
}) {
  const trimmed = content.trim();
  if (!trimmed) return null;

  const markdown = normalize ? normalizeChatMarkdown(trimmed) : trimmed;

  return (
    <div className={["markdown-preview", className].filter(Boolean).join(" ")}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
});
