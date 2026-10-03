import { Button } from "../ui/Button";
import "./QuickReplies.css";

/**
 * One-click answers to the reply on screen, drawn just above the composer.
 *
 * Draws nothing when there are none: an empty row would read as replies still
 * loading.
 */
export function QuickReplies({
  prompts,
  disabled,
  onPick,
}: {
  prompts: readonly string[];
  /** A turn is in flight; a reply now would be refused as a conflict. */
  disabled: boolean;
  onPick: (prompt: string) => void;
}) {
  if (prompts.length === 0) return null;
  return (
    <div className="lg-quick-replies" role="group" aria-label="Quick replies">
      {prompts.map((prompt) => (
        <Button
          key={prompt}
          variant="plain"
          className="lg-quick-reply"
          disabled={disabled}
          title={disabled ? "Baxter is still answering" : undefined}
          onClick={() => onPick(prompt)}
        >
          {prompt}
        </Button>
      ))}
    </div>
  );
}
