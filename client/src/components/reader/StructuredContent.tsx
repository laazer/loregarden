import { MarkdownContent } from "../chat/MarkdownContent";
import {
  asDiffLines,
  asKeyValueRows,
  asRecordTable,
  formatScalar,
  humanizeKey,
  isRecord,
  isRedundantRow,
  isScalar,
  recordLabel,
  splitRecord,
  unwrapSingleRecord,
  type DiffLine,
  type JsonScalar,
  type KeyValueRow,
  type RecordTable,
} from "../../lib/structuredContent";
import "./Reader.css";

/**
 * Any JSON value laid out as a document: short facts as a label/value list,
 * prose under headings as rendered markdown, lists as lists — and the shapes
 * that recur across artifacts in their own layout: `{k, v}` rows as a
 * definition list, uniform records as a table, diff lines as a diff. Replaces
 * the escaped `JSON.stringify` dump every artifact body used to arrive as.
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

function KeyValueList({ rows }: { rows: KeyValueRow[] }) {
  return (
    <dl className="sc-facts">
      {rows.map((row, index) => (
        <div key={`${row.k}-${index}`} className="sc-fact">
          <dt>{humanizeKey(row.k)}</dt>
          <dd>{formatScalar(row.v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Cell({ value }: { value: JsonScalar }) {
  if (typeof value === "boolean") {
    return <span className={value ? "sc-yes" : "sc-no"}>{formatScalar(value)}</span>;
  }
  return <>{formatScalar(value)}</>;
}

function RecordTableView({ table }: { table: RecordTable }) {
  return (
    <div className="sc-table-wrap">
      <table className="sc-table">
        <thead>
          <tr>
            {table.columns.map((column) => (
              <th key={column} scope="col">
                {humanizeKey(column)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, index) => (
            <tr key={index}>
              {table.columns.map((column) => (
                <td key={column} className={typeof row[column] === "number" ? "sc-num" : undefined}>
                  <Cell value={row[column] ?? null} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const DIFF_MARK: Record<string, string> = { a: "+", d: "-", c: " ", h: "" };

function DiffView({ lines }: { lines: DiffLine[] }) {
  return (
    <pre className="sc-diff">
      {lines.map((line, index) => (
        <span key={index} className={`sc-diff-line sc-diff-line--${line.type}`}>
          {DIFF_MARK[line.type]}
          {line.text}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

function RawJson({ value }: { value: unknown }) {
  return <pre className="sc-raw">{JSON.stringify(value, null, 2)}</pre>;
}

function ListValue({ items, depth }: { items: unknown[]; depth: number }) {
  const diff = asDiffLines(items);
  if (diff) return <DiffView lines={diff} />;
  const pairs = asKeyValueRows(items);
  if (pairs) return <KeyValueList rows={pairs} />;
  const table = asRecordTable(items);
  if (table) return <RecordTableView table={table} />;
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

  const inner = unwrapSingleRecord(value);
  if (inner) return <StructuredValue value={inner} depth={depth} omitKey={omitKey} />;

  const label = omitKey === "label" ? recordLabel(value) : null;
  const { facts, sections: allSections } = splitRecord(value);
  // `{k, v}` rows are facts written as a list, so they join the facts rather
  // than sit under a "Rows" heading. A stage report carries its status and
  // confidence twice — as fields and again as rows — so each shows once.
  const factKeys = new Set(facts.map(([key]) => key));
  const rowFacts: [string, JsonScalar][] = [];
  const sections: [string, unknown][] = [];
  for (const [key, section] of allSections) {
    const rows = asKeyValueRows(section);
    if (!rows) {
      sections.push([key, section]);
      continue;
    }
    for (const row of rows) {
      if (!isRedundantRow(row, factKeys)) rowFacts.push([row.k, row.v]);
    }
  }
  const shownFacts = [...facts, ...rowFacts].filter(([, fact]) => !label || fact !== label);

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
