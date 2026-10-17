/**
 * Write text to the clipboard, falling back to a hidden textarea.
 *
 * `navigator.clipboard` is unavailable on insecure origins and rejects when the
 * document is not focused, which is exactly when a menu item is being clicked.
 *
 * A refusal rejects. The fallback used to discard `execCommand`'s boolean, so a
 * browser that declined the write resolved as a success: the caller said
 * "Copied", no toast was pushed, and the operator's clipboard held whatever it
 * held before — "it failed" and "it worked" collapsed into one answer. A real
 * browser signals a refusal with `false` rather than with a throw, which is why
 * every branch below is accounted for.
 */
export async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    // silent-ok: the async API is absent (insecure origin, some WebViews) or
    // refused the write, and the textarea below is the real alternate path —
    // whose own failure rejects rather than being swallowed.
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  try {
    textarea.select();
    // The reason names the clipboard and never the value: `describeError` puts
    // this string in a toast, and an 86-character path in a toast title is its
    // own defect.
    if (typeof document.execCommand !== "function" || !document.execCommand("copy")) {
      throw new Error("The clipboard refused the copy");
    }
  } finally {
    textarea.remove();
  }
}
