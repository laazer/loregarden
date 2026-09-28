import { MarkdownContent } from "../chat/MarkdownContent";
import {
  formatScalar,
  humanizeKey,
  isRecord,
  isScalar,
  recordLabel,
  splitRecord,
  type JsonScalar,
} from "../../lib/structuredContent";
import "./Reader.css";

/**
 * Any JSON value laid out as a document: short facts in a grid, prose under
 * headings as rendered markdown, lists as lists. Replaces the escaped
 * `JSON.stringify` dump every artifact body used to arrive as.
 *
 * Past `MAX_DEPTH` it gives up on layout and shows the remainder as JSON —
 * deeper than that the headings stop helping and start indenting.
 */
const MAX_DEPTH = 3;

function Facts({ facts }: { facts: [string, JsonScalar][] }) {
  if (!facts.length) return null;
  return (
    <dl className="sc-facts">
      {facts.map(([key, value]) => (
        <div key={key} className="sc-fact">
          <dt>{humanizeKey(key)}</dt>
          <dd>{formatScalar(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function RawJson({ value }: { value: unknown }) {
  return <pre className="sc-raw">{JSON.stringify(value, null, 2)}</pre>;
}

function ListValue({ items, depth }: { items: unknown[]; depth: number }) {
  // A list of strings is one markdown list — each item keeps its inline
  // formatting (`code`, **bold**) and the list reads as one block.
  if (items.every((item) => typeof item === "string")) {
    const markdown = (items as string[]).map((item) => `- ${item.replace(/\n/g, "\n  ")}`).join("\n");
    return <MarkdownContent content={markdown} normalize={false} expandable={false} />;
  }
  if (items.every(isScalar)) {
    return (
      <ul className="sc-list">
        {(items as JsonScalar[]).map((item, index) => (
          <li key={index}>{formatScalar(item)}</li>
        ))}
      </ul>
    );
  }
  return (
    <ol className="sc-cards">
      {items.map((item, index) => {
        const label = isRecord(item) ? recordLabel(item) : null;
        return (
          <li key={index} className="sc-card">
            {label && <div className="sc-card-label">{label}</div>}
            <StructuredValue value={item} depth={depth + 1} omitKey={label ? "label" : undefined} />
          </li>
        );
      })}
    </ol>
  );
}

function StructuredValue({
  value,
  depth,
  omitKey,
}: {
  value: unknown;
  depth: number;
  /** When a card already shows its label, the field it came from is not repeated. */
  omitKey?: "label";
}) {
  if (typeof value === "string") {
    return <MarkdownContent content={value} normalize={false} expandable={false} />;
  }
  if (isScalar(value)) return <span className="sc-scalar">{formatScalar(value)}</span>;
  if (depth > MAX_DEPTH) return <RawJson value={value} />;
  if (Array.isArray(value)) return <ListValue items={value} depth={depth} />;
  if (!isRecord(value)) return <RawJson value={value} />;

  const label = omitKey === "label" ? recordLabel(value) : null;
  const { facts, sections } = splitRecord(value);
  const shownFacts = label ? facts.filter(([, fact]) => fact !== label) : facts;

  // One prose field and nothing else is the document — no heading over it.
  if (!shownFacts.length && sections.length === 1 && typeof sections[0][1] === "string") {
    return <StructuredValue value={sections[0][1]} depth={depth} />;
  }

  const Heading = depth === 0 ? "h3" : "h4";
  return (
    <div className="sc-record">
      <Facts facts={shownFacts} />
      {sections.map(([key, section]) => (
        <section key={key} className="sc-section">
          <Heading className="sc-heading">{humanizeKey(key)}</Heading>
          <StructuredValue value={section} depth={depth + 1} />
        </section>
      ))}
    </div>
  );
}

export function StructuredContent({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") {
    return <p className="sc-empty">This document has no content.</p>;
  }
  return (
    <div className="sc-root">
      <StructuredValue value={value} depth={0} />
    </div>
  );
}
