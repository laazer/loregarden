/**
 * Write text to the clipboard, falling back to a hidden textarea.
 *
 * `navigator.clipboard` is unavailable on insecure origins and rejects when the
 * document is not focused, which is exactly when a menu item is being clicked.
 */
export async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    document.body.removeChild(textarea);
  }
}
