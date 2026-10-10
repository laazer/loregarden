import { Button } from "../ui/Button";

/**
 * One-click prompts: a row directly under a chat composer, or a stack in a side column.
 *
 * Under, not above: the row is one line at a fixed height, so a change to what
 * it offers never moves the thread the way the old row between the thread and
 * the composer did.
 */
export function ChatPromptRow({
  prompts,
  disabled,
  onPick,
  stacked = false,
}: {
  prompts: readonly string[];
  /** A turn is in flight; a prompt now would be refused as a conflict. */
  disabled: boolean;
  onPick: (prompt: string) => void;
  /** One per line, wrapping — for a narrow column such as the workbench. */
  stacked?: boolean;
}) {
  if (prompts.length === 0) return null;
  return (
    <div
      className={`lg-chat-prompt-row${stacked ? " lg-chat-prompt-row--stacked" : ""}`}
      role="group"
      aria-label="Suggested prompts"
    >
      {prompts.map((prompt) => (
        <Button
          key={prompt}
          variant="plain"
          className="lg-chat-prompt"
          title={disabled ? "Baxter is still answering" : prompt}
          disabled={disabled}
          onClick={() => onPick(prompt)}
        >
          {prompt}
        </Button>
      ))}
    </div>
  );
}
