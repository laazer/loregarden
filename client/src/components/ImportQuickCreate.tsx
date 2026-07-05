import { useState, type ReactNode } from "react";

interface ImportQuickCreateProps {
  label: string;
  placeholder: string;
  actionLabel: string;
  disabled?: boolean;
  onSubmit: (title: string) => void;
  extra?: ReactNode;
  extraRequired?: boolean;
}

export function ImportQuickCreate({
  label,
  placeholder,
  actionLabel,
  disabled = false,
  onSubmit,
  extra,
  extraRequired = false,
}: ImportQuickCreateProps) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");

  const canSubmit = title.trim().length > 0 && !disabled && !extraRequired;

  const handleSubmit = () => {
    if (!canSubmit) return;
    onSubmit(title.trim());
    setTitle("");
    setOpen(false);
  };

  if (!open) {
    return (
      <button
        type="button"
        className="btn-secondary btn-compact import-quick-create-toggle"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        {label}
      </button>
    );
  }

  return (
    <div className="import-quick-create">
      <div className="import-quick-create-row">
        <input
          className="btn-secondary filter-select"
          style={{ flex: 1, fontSize: 12 }}
          value={title}
          disabled={disabled}
          placeholder={placeholder}
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              handleSubmit();
            }
            if (event.key === "Escape") {
              setOpen(false);
              setTitle("");
            }
          }}
        />
        <button
          type="button"
          className="btn-primary btn-compact"
          disabled={!canSubmit}
          onClick={handleSubmit}
        >
          {actionLabel}
        </button>
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={disabled}
          onClick={() => {
            setOpen(false);
            setTitle("");
          }}
        >
          Cancel
        </button>
      </div>
      {extra}
    </div>
  );
}
