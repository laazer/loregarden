import { memo, type ReactNode } from "react";
import type { Components, ExtraProps } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { isLongProse } from "../../lib/structuredContent";
import { openReader } from "../../state/readerStore";
import { normalizeChatMarkdown } from "./chatUtils";
import "../reader/Reader.css";

type PreNode = ExtraProps["node"];

/**
 * The card kind of a `loregarden` fence the server could not parse, or null for
 * any other code block.
 *
 * A fence only reaches markdown when its JSON failed validation — parsed cards
 * become parts and never render as prose.
 */
function unparsedCardKind(node: PreNode): string | null {
  const code = node?.children[0];
  if (code?.type !== "element" || code.tagName !== "code") return null;
  const classes = code.properties.className;
  if (!Array.isArray(classes) || !classes.includes("language-loregarden")) return null;
  const text = code.children.map((child) => (child.type === "text" ? child.value : "")).join("");
  const kind = /"primitive"\s*:\s*"([\w-]+)"/.exec(text)?.[1];
  return kind ? kind.replace(/_/g, " ") : "unknown";
}

/** Code blocks, except a card fence the server could not parse — that one folds. */
export function MarkdownPre({ node, children }: { node?: PreNode; children?: ReactNode }) {
  const kind = unparsedCardKind(node);
  if (kind === null) return <pre>{children}</pre>;
  // Collapsed rather than dropped: the operator can still read what was meant,
  // but a page of JSON no longer stands in for the card.
  return (
    <details className="md-unparsed-card">
      <summary>A {kind} card couldn&apos;t be shown — show its raw data</summary>
      <pre>{children}</pre>
    </details>
  );
}

const markdownComponents: Components = {
  pre: MarkdownPre,
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
