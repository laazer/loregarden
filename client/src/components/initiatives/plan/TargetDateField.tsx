import { useEffect, useState } from "react";

import { Input } from "../../ui/Input";

/**
 * A target date that saves when the operator is done with it — on blur or
 * Enter — rather than on every keystroke, since a typed date passes through
 * several valid-looking dates on the way to the one meant. Escape reverts.
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
  onCommit: (date: string | null) => void;
}) {
  const [text, setText] = useState(value ?? "");
  useEffect(() => setText(value ?? ""), [value]);

  const commit = () => {
    const next = text || null;
    if (next !== value) onCommit(next);
  };

  return (
    <Input
      type="date"
      className="plan-date-input"
      aria-label={label}
      value={text}
      disabled={disabled}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setText(value ?? "");
      }}
    />
  );
}
