import type { TerminalPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenIdeButton } from "./ResourceActionButton";

export function TerminalPrimitive({ part }: { part: TerminalPart }) {
  const lines = part.lines ?? [];
  return (
    <PrimitiveCard
      title={part.title ?? "Terminal"}
      subtitle={part.cwd ?? undefined}
      actions={part.cwd ? <OpenIdeButton /> : null}
    >
      <div className="lg-primitive-terminal">
        <div className="lg-primitive-terminal-bar">
          <span>●</span>
          <span>{part.title ?? "Terminal"}</span>
        </div>
        <pre className="lg-primitive-terminal-body">
          {lines.map((line, i) => {
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
