import type { CSSProperties, ReactNode } from "react";
import { useState } from "react";

import { usePrimitiveFrame } from "./primitiveFrame";
import "./PrimitiveCard.css";

export type PrimitiveCardTone = "default" | "accent" | "warn" | "danger" | "ok";

export function PrimitiveCard({
  title,
  subtitle,
  meta,
  tone = "default",
  accent,
  icon,
  statusDot,
  actions,
  resourceAction,
  collapsible = false,
  defaultCollapsed = false,
  loading = false,
  error,
  children,
  className,
  /** When set, replaces the default title/subtitle/meta block (actions stay top-right). */
  header,
}: {
  title: string;
  subtitle?: string;
  meta?: ReactNode;
  tone?: PrimitiveCardTone;
  /** Colour for the card's left edge, the v6 way of carrying a record's status. */
  accent?: string | null;
  icon?: ReactNode;
  statusDot?: string;
  actions?: ReactNode;
  /** External-resource links. Always rendered last in the header, top right. */
  resourceAction?: ReactNode;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  loading?: boolean;
  error?: string | null;
  children?: ReactNode;
  className?: string;
  header?: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const frame = usePrimitiveFrame();
  const expanded = frame?.expanded ?? false;
  const bodyHidden = collapsible && collapsed && !expanded;
  const hasHeaderChrome =
    Boolean(header) ||
    Boolean(icon) ||
    Boolean(statusDot) ||
    Boolean(title) ||
    Boolean(subtitle) ||
    Boolean(meta) ||
    collapsible ||
    Boolean(frame?.canExpand) ||
    Boolean(resourceAction);

  return (
    <article
      className={[
        "lg-primitive-card",
        `lg-primitive-card--${tone}`,
        accent ? "lg-primitive-card--accented" : null,
        expanded ? "lg-primitive-card--expanded" : null,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      style={accent ? ({ "--lg-primitive-accent": accent } as CSSProperties) : undefined}
    >
      {hasHeaderChrome ? (
        <header className="lg-primitive-card-header">
          {icon ? <span className="lg-primitive-card-icon" aria-hidden>{icon}</span> : null}
          {statusDot ? (
            <span
              className="lg-primitive-card-dot"
              style={{ background: statusDot }}
              aria-hidden
            />
          ) : null}
          {header ?? (
            <div className="lg-primitive-card-titles">
              {title ? <h3 className="lg-primitive-card-title">{title}</h3> : null}
              {subtitle ? <p className="lg-primitive-card-sub">{subtitle}</p> : null}
              {meta ? <div className="lg-primitive-card-meta">{meta}</div> : null}
            </div>
          )}
          <div className="lg-primitive-card-header-actions">
            {collapsible ? (
              <button
                type="button"
                className="lg-primitive-card-collapse"
                aria-expanded={!collapsed}
                onClick={() => setCollapsed((v) => !v)}
              >
                {collapsed ? "Show" : "Hide"}
              </button>
            ) : null}
            {frame?.canExpand ? (
              <button
                type="button"
                className="lg-primitive-card-collapse"
                aria-pressed={expanded}
                title={expanded ? "Collapse to the thread (Esc)" : "Expand to full screen"}
                onClick={frame.toggleExpanded}
              >
                {expanded ? "Shrink" : "Expand"}
              </button>
            ) : null}
            {resourceAction}
          </div>
        </header>
      ) : null}
      {error ? <p className="lg-primitive-card-error" role="alert">{error}</p> : null}
      {loading && !error ? <p className="lg-primitive-card-loading">Loading…</p> : null}
      {!bodyHidden && !loading && !error && children ? (
        <div className="lg-primitive-card-body">{children}</div>
      ) : null}
      {actions && !bodyHidden ? (
        <footer className="lg-primitive-card-actions">{actions}</footer>
      ) : null}
    </article>
  );
}
