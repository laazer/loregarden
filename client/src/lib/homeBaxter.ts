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
    /* private mode — chat still opens; prompt just won't prefill */
  }
}
