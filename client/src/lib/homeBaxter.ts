/** Home hero → Baxter chat prompt hand-off. */
export const HOME_BAXTER_PROMPT_KEY = "loregarden.homeBaxterPrompt";

/** @deprecated Prefer HOME_BAXTER_PROMPT_KEY */
export const HOME_BAXTER_BRIEF_KEY = HOME_BAXTER_PROMPT_KEY;

export function chatPath(): string {
  return "/chat";
}

export function takeHomeBaxterPrompt(): string {
  try {
    const prompt = sessionStorage.getItem(HOME_BAXTER_PROMPT_KEY)?.trim() ?? "";
    if (prompt) sessionStorage.removeItem(HOME_BAXTER_PROMPT_KEY);
    return prompt;
  } catch {
    return "";
  }
}

export function stashHomeBaxterPrompt(prompt: string): void {
  const content = prompt.trim();
  if (!content) return;
  try {
    sessionStorage.setItem(HOME_BAXTER_PROMPT_KEY, content);
  } catch {
    /* silent-ok: private mode blocks sessionStorage; the chat still opens, the
       draft simply does not prefill, and the user retypes it. */
  }
}

/**
 * Files attached on Home, carried to the chat page with the prompt.
 *
 * Held in memory rather than beside the prompt in sessionStorage: a `File`
 * does not serialise, and the hand-off is one in-app navigation. A reload in
 * between drops them, which is also what a reload does to any composer's tray.
 */
let stashedFiles: File[] = [];

export function stashHomeBaxterFiles(files: File[]): void {
  stashedFiles = [...files];
}

export function takeHomeBaxterFiles(): File[] {
  const files = stashedFiles;
  stashedFiles = [];
  return files;
}
