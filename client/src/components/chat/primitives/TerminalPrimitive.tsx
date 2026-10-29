import type { TerminalLine, TerminalPart } from "./types";
import type { ChipStatus } from "../../../lib/statusTone";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenIdeButton } from "./ResourceActionButton";

/** The run's exit, from its last status line ("exit 0"), when it has one. */
function exitOf(lines: TerminalLine[]): { text: string; status: ChipStatus | null } {
  const last = [...lines].reverse().find((line) => line.kind === "status");
  if (!last) return { text: "", status: null };
  const code = /\bexit\w*\D*?(-?\d+)/i.exec(last.text)?.[1];
  if (code === undefined) return { text: last.text, status: null };
  return {
    text: last.text,
    status: code === "0" ? { tone: "done", label: "Passed" } : { tone: "bad", label: `Exited ${code}` },
  };
}

/**
 * A command and what it printed, in mono on the darkest ground. How it exited
 * is the chip; the footer repeats the exit line and opens the workspace's IDE.
 */
export function TerminalPrimitive({ part }: { part: TerminalPart }) {
  const lines = part.lines ?? [];
  const exit = exitOf(lines);
  return (
    <PrimitiveCard
      kind="terminal"
      title={part.title ?? "Terminal"}
      refLine={part.cwd}
      copyRef={part.cwd}
      status={exit.status}
      footerHint={exit.text || null}
      actions={part.cwd ? <OpenIdeButton /> : null}
    >
      <div className="lg-primitive-terminal">
        <pre className="lg-primitive-terminal-body">
          {lines
            .filter((line) => line.kind !== "status")
            .map((line, i) => {
              const kind = line.kind ?? "stdout";
              const cls =
                kind === "command"
                  ? "lg-primitive-terminal-cmd"
                  : kind === "stderr"
                    ? "lg-primitive-terminal-err"
                    : undefined;
              const prefix = kind === "command" ? "$ " : "";
              return (
                <div key={`${i}-${line.text.slice(0, 12)}`} className={cls}>
                  {prefix}
                  {line.text}
                </div>
              );
            })}
        </pre>
      </div>
    </PrimitiveCard>
  );
}
