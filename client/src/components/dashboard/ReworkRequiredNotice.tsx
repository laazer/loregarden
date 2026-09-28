import { MarkdownContent } from "../chat/MarkdownContent";

/** The reviewer's rework reasons on the ticket detail — markdown, openable in the reader. */
export function ReworkRequiredNotice({ text }: { text: string | null | undefined }) {
  if (!text?.trim()) return null;
  return (
    <div
      style={{
        marginTop: 12,
        padding: "10px 12px",
        borderRadius: 11,
        background: "rgba(199,125,45,.08)",
        border: "1px solid rgba(199,125,45,.28)",
        fontSize: 12,
        color: "var(--orl, #c77d2d)",
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>Rework required</div>
      <MarkdownContent content={text} readerTitle="Rework required" />
    </div>
  );
}
