export function formatJsonForDisplay(raw: string | undefined | null): string {
  const text = raw?.trim();
  if (!text || text === "{}") return "";

  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}
