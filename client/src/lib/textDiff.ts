import type { DiffArtifact, DiffLine } from "../api/types";

function splitLines(text: string): string[] {
  if (!text) return [];
  const parts = text.split("\n");
  if (parts.length > 1 && parts[parts.length - 1] === "") {
    parts.pop();
  }
  return parts;
}

/** Classic LCS table for line-level diffs. Fine for role-file / patch sized payloads. */
function lcsTable(a: string[], b: string[]): number[][] {
  const rows = a.length + 1;
  const cols = b.length + 1;
  const table: number[][] = Array.from({ length: rows }, () => Array(cols).fill(0));
  for (let i = 1; i < rows; i += 1) {
    for (let j = 1; j < cols; j += 1) {
      table[i][j] =
        a[i - 1] === b[j - 1]
          ? table[i - 1][j - 1] + 1
          : Math.max(table[i - 1][j], table[i][j - 1]);
    }
  }
  return table;
}

type DiffOp =
  | { kind: "c"; text: string; oldLn: number; newLn: number }
  | { kind: "a"; text: string; newLn: number }
  | { kind: "d"; text: string; oldLn: number };

function lineOps(left: string[], right: string[]): DiffOp[] {
  const table = lcsTable(left, right);
  const reverse: DiffOp[] = [];
  let i = left.length;
  let j = right.length;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && left[i - 1] === right[j - 1]) {
      reverse.push({ kind: "c", text: left[i - 1], oldLn: i, newLn: j });
      i -= 1;
      j -= 1;
    } else if (j > 0 && (i === 0 || table[i][j - 1] >= table[i - 1][j])) {
      reverse.push({ kind: "a", text: right[j - 1], newLn: j });
      j -= 1;
    } else {
      reverse.push({ kind: "d", text: left[i - 1], oldLn: i });
      i -= 1;
    }
  }
  return reverse.reverse();
}

/**
 * Build a DiffArtifact (one section) from two text blobs so chat edit cards can
 * reuse the inline review chrome without a ticket/branch backend.
 */
export function buildTextDiffArtifact(
  path: string,
  original: string,
  modified: string,
): DiffArtifact {
  const left = splitLines(original);
  const right = splitLines(modified);
  const ops = lineOps(left, right);
  let add = 0;
  let del = 0;
  const lines: DiffLine[] = [];

  if (ops.length) {
    lines.push({
      type: "h",
      ln: "",
      text: `@@ -1,${left.length || 0} +1,${right.length || 0} @@`,
    });
  }

  for (const op of ops) {
    if (op.kind === "c") {
      lines.push({ type: "c", ln: String(op.oldLn), text: op.text });
    } else if (op.kind === "a") {
      add += 1;
      lines.push({ type: "a", ln: String(op.newLn), text: op.text });
    } else {
      del += 1;
      lines.push({ type: "d", ln: String(op.oldLn), text: op.text });
    }
  }

  const fileLabel = path || "proposed edit";
  return {
    file: fileLabel,
    add: `+${add}`,
    del: `−${del}`,
    files: "1 file",
    file_entries: [{ path: fileLabel, add, del }],
    sections: [{ path: fileLabel, add, del, lines }],
  };
}

export type EditDiffComment = {
  file_path: string;
  line_index: number;
  line_kind: string;
  line_number: string;
  line_text: string;
  content: string;
};

/** Format local edit-card comments into a Baxter chat message. */
export function formatEditCommentsForChat(args: {
  title: string;
  path: string;
  comments: EditDiffComment[];
  instructions?: string;
}): string {
  const header = `## Diff review: ${args.title}`;
  const pathLine = args.path ? `File: \`${args.path}\`` : null;
  const body = args.comments
    .map((comment) => {
      const kind =
        comment.line_kind === "a" ? "+" : comment.line_kind === "d" ? "−" : " ";
      const snippet = comment.line_text.trim() || "(empty line)";
      return (
        `- L${comment.line_number || comment.line_index + 1} (${kind}) \`${snippet.slice(0, 80)}\`\n` +
        `  ${comment.content.trim()}`
      );
    })
    .join("\n");
  const instructions = args.instructions?.trim()
    ? `\n\n${args.instructions.trim()}`
    : "";
  return [header, pathLine, "", "Please resolve these inline review comments:", body, instructions]
    .filter((line) => line !== null)
    .join("\n")
    .trim();
}
