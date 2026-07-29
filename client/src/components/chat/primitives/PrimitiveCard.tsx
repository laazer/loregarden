import type { ReactNode } from "react";
import { useState } from "react";

import { usePrimitiveFrame } from "./primitiveFrame";
import "./PrimitiveCard.css";

export type PrimitiveCardTone = "default" | "accent" | "warn" | "danger" | "ok";

export function PrimitiveCard({
  title,
  subtitle,
  meta,
  tone = "default",
  icon,
  statusDot,
  actions,
  collapsible = false,
  defaultCollapsed = false,
  loading = false,
  error,
  children,
  className,
}: {
  title: string;
  subtitle?: string;
  meta?: ReactNode;
  tone?: PrimitiveCardTone;
  icon?: ReactNode;
  statusDot?: string;
  actions?: ReactNode;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  loading?: boolean;
  error?: string | null;
  children?: ReactNode;
  className?: string;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const frame = usePrimitiveFrame();
  const expanded = frame?.expanded ?? false;
  const bodyHidden = collapsible && collapsed && !expanded;

  return (
    <article
      className={[
        "lg-primitive-card",
        `lg-primitive-card--${tone}`,
        expanded ? "lg-primitive-card--expanded" : null,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <header className="lg-primitive-card-header">
        {icon ? <span className="lg-primitive-card-icon" aria-hidden>{icon}</span> : null}
        {statusDot ? (
          <span
            className="lg-primitive-card-dot"
            style={{ background: statusDot }}
            aria-hidden
          />
        ) : null}
        <div className="lg-primitive-card-titles">
          <h3 className="lg-primitive-card-title">{title}</h3>
          {subtitle ? <p className="lg-primitive-card-sub">{subtitle}</p> : null}
          {meta ? <div className="lg-primitive-card-meta">{meta}</div> : null}
        </div>
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
        </div>
      </header>
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
