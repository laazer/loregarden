import type { ReactNode } from "react";

import { copyText } from "../../../lib/clipboard";
import type { ChipStatus } from "../../../lib/statusTone";
import { describeError, pushToast } from "../../../state/toastStore";
import { OverflowMenu, OverflowMenuItem } from "../../OverflowMenu";
import { Button } from "../../ui/Button";
import { StatusChip } from "../../ui/StatusChip";
import { usePrimitiveFrame } from "./primitiveFrame";
import { primitiveIconPath, primitiveTint, type PrimitiveTint } from "./primitiveKinds";
import "./PrimitiveCard.css";

/** The kind's glyph, drawn in the 16px circle at the head of its card. */
function PrimitiveKindIcon({ kind }: { kind: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={primitiveIconPath(kind)} />
    </svg>
  );
}

function ExpandIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d={expanded ? "M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" : "M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"} />
    </svg>
  );
}

/**
 * Every chat card's one anatomy: a head (the kind's icon in a tinted circle,
 * the title, a ref line naming what the card points at, the status chip, then
 * Expand, Open and ⋯), a body laid out by its content, and a footer holding at
 * most one action with a line saying what it does.
 *
 * State lives in the chip, never in the card's border, so a ticket, a terminal
 * and a question read as the same kind of object and differ only where their
 * content does. A card that only shows something has no footer.
 */
export function PrimitiveCard({
  kind,
  tint,
  title,
  lead,
  refLine,
  subtitle,
  status,
  meta,
  resourceAction,
  headerActions,
  menu,
  copyRef,
  footerHint,
  actions,
  loading = false,
  skeletonLines = 2,
  error,
  children,
  className,
}: {
  /** The primitive kind: picks the icon and the circle's default tint. */
  kind: string;
  tint?: PrimitiveTint;
  title: string;
  /** Before the title, such as a ticket's priority badge. */
  lead?: ReactNode;
  /** What the card points at, in mono: an id, a path, a branch. */
  refLine?: string | null;
  /** A line of prose under the title, for a card with no ref to name. */
  subtitle?: string | null;
  status?: ChipStatus | null;
  /** Key facts as a row at the top of the body. */
  meta?: ReactNode;
  /** Leaves for the record's own page: the head's Open control. */
  resourceAction?: ReactNode;
  /** Further head icons a card's content needs, such as zoom. */
  headerActions?: ReactNode;
  /** Secondary actions, in the head's ⋯ menu. */
  menu?: ReactNode;
  /** Adds "Copy ref" to the ⋯ menu. */
  copyRef?: string | null;
  /** What the footer's action does, or what the card is waiting for. */
  footerHint?: ReactNode;
  /** The footer's one action. */
  actions?: ReactNode;
  loading?: boolean;
  /** Lines of skeleton while loading, to hold the height the body will take. */
  skeletonLines?: number;
  error?: string | null;
  children?: ReactNode;
  className?: string;
}) {
  const frame = usePrimitiveFrame();
  const expanded = frame?.expanded ?? false;
  const resolvedTint = tint ?? primitiveTint(kind);
  const showMenu = Boolean(menu) || Boolean(copyRef);

  const copy = async () => {
    if (!copyRef) return;
    try {
      await copyText(copyRef);
      pushToast({ tone: "success", title: "Copied", message: copyRef });
    } catch (err) {
      pushToast({ tone: "error", title: "Couldn't copy the ref", message: describeError(err, "Clipboard unavailable") });
    }
  };

  return (
    <article
      className={["lg-primitive-card", expanded ? "lg-primitive-card--expanded" : null, className]
        .filter(Boolean)
        .join(" ")}
      aria-busy={loading || undefined}
    >
      <header className="lg-primitive-card-header">
        <span className={`lg-primitive-card-icon lg-primitive-card-icon--${resolvedTint}`} aria-hidden>
          <PrimitiveKindIcon kind={kind} />
        </span>
        <div className="lg-primitive-card-titles">
          <h3 className="lg-primitive-card-title">
            {lead}
            <span className="lg-primitive-card-title-text">{title}</span>
          </h3>
          {refLine ? (
            <p className="lg-primitive-card-ref" title={refLine}>
              {refLine}
            </p>
          ) : subtitle ? (
            <p className="lg-primitive-card-sub">{subtitle}</p>
          ) : null}
        </div>
        <div className="lg-primitive-card-header-actions">
          {status ? <StatusChip tone={status.tone} label={status.label} /> : null}
          {headerActions}
          {frame?.canExpand ? (
            <Button
              variant="plain"
              className="lg-primitive-card-icon-btn"
              aria-label={expanded ? "Shrink" : "Expand"}
              aria-pressed={expanded}
              title={expanded ? "Back to the thread (Esc)" : "Expand to full screen"}
              onClick={frame.toggleExpanded}
            >
              <ExpandIcon expanded={expanded} />
            </Button>
          ) : null}
          {resourceAction}
          {showMenu ? (
            <OverflowMenu label={`More for ${title}`} align="right">
              {menu}
              {copyRef ? <OverflowMenuItem onSelect={() => void copy()}>Copy ref</OverflowMenuItem> : null}
            </OverflowMenu>
          ) : null}
        </div>
      </header>
      {error ? (
        <p className="lg-primitive-card-error" role="alert">
          {error}
        </p>
      ) : null}
      {loading && !error ? (
        <div className="lg-primitive-card-skeleton" aria-label={`Loading ${title}`}>
          {Array.from({ length: skeletonLines }, (_, i) => (
            <span key={i} className="lg-primitive-card-skeleton-line" />
          ))}
        </div>
      ) : null}
      {!loading && !error && (meta || children) ? (
        <div className="lg-primitive-card-body">
          {meta ? <div className="lg-primitive-card-meta">{meta}</div> : null}
          {children}
        </div>
      ) : null}
      {actions || footerHint ? (
        <footer className="lg-primitive-card-actions">
          {footerHint ? <span className="lg-primitive-card-hint">{footerHint}</span> : null}
          {actions ? <div className="lg-primitive-card-footer-actions">{actions}</div> : null}
        </footer>
      ) : null}
    </article>
  );
}
