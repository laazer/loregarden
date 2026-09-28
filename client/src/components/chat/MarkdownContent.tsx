import { memo } from "react";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { isLongProse } from "../../lib/structuredContent";
import { openReader } from "../../state/readerStore";
import { normalizeChatMarkdown } from "./chatUtils";
import "../reader/Reader.css";

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

/**
 * Markdown at glance size. Every surface that shows agent prose renders
 * through here, so every surface gets the same way out when the prose is
 * long: an "Open" control under it that hands the text to the reader dialog
 * (`ReaderHost`) at reading size. Callers pass `readerTitle` to name what is
 * being read; `expandable={false}` is for content already inside the reader.
 */
export const MarkdownContent = memo(function MarkdownContent({
  content,
  className,
  normalize = true,
  expandable = true,
  readerTitle,
  readerSubtitle,
}: {
  content: string;
  className?: string;
  normalize?: boolean;
  expandable?: boolean;
  readerTitle?: string;
  readerSubtitle?: string;
}) {
  const trimmed = content.trim();
  if (!trimmed) return null;

  const markdown = normalize ? normalizeChatMarkdown(trimmed) : trimmed;
  const rendered = (
    <div className={["markdown-preview", className].filter(Boolean).join(" ")}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {markdown}
      </ReactMarkdown>
    </div>
  );

  if (!expandable || !isLongProse(markdown)) return rendered;

  const title = readerTitle ?? "Document";
  return (
    <div className="md-expandable">
      {rendered}
      <button
        type="button"
        className="md-open-btn"
        aria-label={`Open ${title} in the reader`}
        onClick={(event) => {
          // Inline markdown often sits inside a clickable row or card.
          event.stopPropagation();
          openReader({ title, subtitle: readerSubtitle, content: markdown });
        }}
      >
        Open ↗
      </button>
    </div>
  );
});
