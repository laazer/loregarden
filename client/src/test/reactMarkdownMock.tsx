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

function splitTableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isTableBlock(block: string): boolean {
  const lines = block.split("\n").filter((line) => line.trim().length > 0);
  return lines.length >= 2 && lines.every((line) => line.trim().startsWith("|"));
}

function renderTableBlock(block: string, key: number): ReactNode {
  const lines = block.split("\n").filter((line) => line.trim().length > 0);
  const headerCells = splitTableCells(lines[0]);
  const bodyLines = lines.slice(2);

  return (
    <div key={key} className="markdown-table-wrap">
      <table>
        <thead>
          <tr>
            {headerCells.map((cell, cellIndex) => (
              <th key={cellIndex}>{renderInlineMarkdown(cell)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bodyLines.map((line, rowIndex) => {
            const cells = splitTableCells(line);
            return (
              <tr key={rowIndex}>
                {cells.map((cell, cellIndex) => (
                  <td key={cellIndex}>{renderInlineMarkdown(cell)}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function ReactMarkdown({ children }: { children?: string }) {
  const text = String(children ?? "");
  const blocks = text.split(/\n{2,}/);

  return (
    <div>
      {blocks.map((block, index) =>
        isTableBlock(block) ? (
          renderTableBlock(block, index)
        ) : (
          <p key={index}>{renderInlineMarkdown(block.replace(/  \n/g, "\n"))}</p>
        ),
      )}
    </div>
  );
}
