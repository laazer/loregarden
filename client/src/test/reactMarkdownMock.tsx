import type { ReactNode } from "react";

function renderInlineMarkdown(text: string): ReactNode[] {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  const parts = text.split(pattern).filter((part) => part.length > 0);

  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index}>{part.slice(1, -1)}</code>;
    }
    return <span key={index}>{part}</span>;
  });
}

export default function ReactMarkdown({ children }: { children?: string }) {
  const text = String(children ?? "");
  const blocks = text.split(/\n{2,}/);

  return (
    <div>
      {blocks.map((block, index) => (
        <p key={index}>{renderInlineMarkdown(block.replace(/  \n/g, "\n"))}</p>
      ))}
    </div>
  );
}
