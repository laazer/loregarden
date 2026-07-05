import { useCallback, useEffect, useRef, useState } from "react";

interface CopyTerminalCommandButtonProps {
  command: string;
  label?: string;
  title?: string;
  className?: string;
  disabled?: boolean;
}

export function CopyTerminalCommandButton({
  command,
  label = "Copy cmd",
  title = "Copy terminal command",
  className = "btn-secondary btn-compact",
  disabled = false,
}: CopyTerminalCommandButtonProps) {
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (resetTimer.current !== null) {
        window.clearTimeout(resetTimer.current);
      }
    };
  }, []);

  const handleCopy = useCallback(async () => {
    if (disabled || !command.trim()) return;
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = command;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }
    setCopied(true);
    if (resetTimer.current !== null) {
      window.clearTimeout(resetTimer.current);
    }
    resetTimer.current = window.setTimeout(() => setCopied(false), 1800);
  }, [command, disabled]);

  return (
    <button
      type="button"
      className={className}
      disabled={disabled}
      title={title}
      aria-label={title}
      onClick={() => void handleCopy()}
    >
      {copied ? "Copied" : label}
    </button>
  );
}
