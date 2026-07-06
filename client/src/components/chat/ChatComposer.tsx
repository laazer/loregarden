import type { ReactNode } from "react";

export function ChatComposer({
  value,
  onChange,
  onSubmit,
  placeholder = "Write a message…",
  isSending = false,
  sendLabel = "Send",
  sendingLabel = "Sending…",
  disabled = false,
  optionsRow,
  actions,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  isSending?: boolean;
  sendLabel?: string;
  sendingLabel?: string;
  disabled?: boolean;
  optionsRow?: ReactNode;
  actions?: ReactNode;
  error?: string | null;
}) {
  const canSend = value.trim().length > 0 && !isSending && !disabled;

  const submit = () => {
    if (!canSend) return;
    onSubmit();
  };

  return (
    <div className="chat-composer">
      {optionsRow ? <div className="chat-composer-options">{optionsRow}</div> : null}
      <textarea
        className="chat-composer-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={3}
        placeholder={placeholder}
        disabled={disabled || isSending}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="chat-composer-toolbar">
        {actions ? <div className="chat-composer-actions">{actions}</div> : <span />}
        <button type="button" className="btn-primary" disabled={!canSend} onClick={submit}>
          {isSending ? sendingLabel : sendLabel}
        </button>
      </div>
      {error ? <div className="chat-composer-error">{error}</div> : null}
    </div>
  );
}
