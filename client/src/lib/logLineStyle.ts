export type LogTagVariant = "info" | "ok" | "run" | "warn" | "err";

export function logTagVariant(tag: string | undefined | null): LogTagVariant {
  switch ((tag ?? "").toUpperCase()) {
    case "OK":
      return "ok";
    case "RUN":
    case "TOOL":
      return "run";
    case "WARN":
      return "warn";
    case "ERR":
    case "FAIL":
      return "err";
    default:
      return "info";
  }
}
