import { useEffect, useId, useState } from "react";

import { Input } from "../../ui/Input";

type SaveState = "idle" | "dirty" | "saving" | "saved" | "failed";

const STATE_TEXT: Record<SaveState, string> = {
  idle: "",
  dirty: "Press Enter to save",
  saving: "Saving…",
  saved: "Saved",
  failed: "Not saved",
};

/**
 * A target date that saves when the operator is done with it — Enter, or
 * leaving the field — and says so. A typed date passes through several
 * valid-looking dates on the way to the one meant, so it does not save on
 * every keystroke; the line under it is how the operator knows the difference
 * between "edited" and "saved". Escape reverts.
 */
export function TargetDateField({
  value,
  label,
  disabled,
  onCommit,
}: {
  value: string | null;
  label: string;
  disabled?: boolean;
  /** Resolves when saved; rejects when not (the caller's toast says why). */
  onCommit: (date: string | null) => Promise<unknown>;
}) {
  const [text, setText] = useState(value ?? "");
  const [state, setState] = useState<SaveState>("idle");
  const statusId = useId();

  useEffect(() => {
    setText(value ?? "");
  }, [value]);

  useEffect(() => {
    if (state !== "saved") return undefined;
    const timer = window.setTimeout(() => setState("idle"), 2500);
    return () => window.clearTimeout(timer);
  }, [state]);

  const commit = () => {
    const next = text || null;
    if (next === value) {
      setState("idle");
      return;
    }
    setState("saving");
    onCommit(next).then(
      () => setState("saved"),
      // silent-ok: the mutation carries meta.errorTitle, so the global toast names
      // the failure; this only marks the field as not saved.
      () => setState("failed"),
    );
  };

  return (
    <span className="plan-date">
      <Input
        type="date"
        className="plan-date-input"
        aria-label={label}
        aria-describedby={statusId}
        value={text}
        disabled={disabled || state === "saving"}
        onChange={(e) => {
          setText(e.target.value);
          setState(e.target.value === (value ?? "") ? "idle" : "dirty");
        }}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") {
            setText(value ?? "");
            setState("idle");
          }
        }}
      />
      <span id={statusId} className={`plan-date-state plan-date-${state}`} aria-live="polite">
        {STATE_TEXT[state]}
      </span>
    </span>
  );
}
