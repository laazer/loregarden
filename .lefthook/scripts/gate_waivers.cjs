/**
 * Line waivers shared by the frontend gates: `ux-ok:`, `theme-ok:`.
 *
 * One contract for every marker: the marker plus a substantive reason, on the
 * flagged span or in the comment block directly above it. A marker with a
 * throwaway reason is itself a finding, so a waiver always records that someone
 * thought about the case.
 */

const MIN_WAIVER_REASON_CHARS = 12;

/**
 * 1-based line numbers that are entirely comment.
 *
 * Computed by a forward pass rather than by per-line prefix matching, because
 * in JSX the only way to comment above an element is `{/* … *\/}`, whose
 * continuation lines start with ordinary prose. The prefix test read the last
 * line of such a block as code and stopped the walk there, so a multi-line JSX
 * waiver silently did not apply — the failure mode a waiver exists to avoid.
 */
function commentLines(lines) {
  const inComment = new Set();
  let open = false;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();
    if (open) {
      inComment.add(i + 1);
      if (line.includes("*/")) open = false;
      continue;
    }
    const blockStart = trimmed.indexOf("/*");
    if (blockStart !== -1 && (trimmed.startsWith("/*") || trimmed.startsWith("{/*"))) {
      inComment.add(i + 1);
      // A block that opens and closes on one line leaves nothing open.
      if (!line.includes("*/", line.indexOf("/*") + 2)) open = true;
      continue;
    }
    if (trimmed.startsWith("//")) inComment.add(i + 1);
  }
  return inComment;
}

/** Waiver checks for one marker over one file's lines. */
function waiversFor(marker, lines) {
  const comments = commentLines(lines);

  function reasonOn(line) {
    const at = line.indexOf(marker);
    if (at === -1) return null;
    return line
      .slice(at + marker.length)
      .replace(/(?:\*\/|\}|\s)*$/, "")
      .trim();
  }

  /** Walk up over the comment block above, where a multi-line reason lives. */
  function startOf(start) {
    let first = start;
    while (first > 1 && comments.has(first - 1)) first -= 1;
    return first;
  }

  function waived(start, end) {
    for (let i = startOf(start); i <= Math.min(end, lines.length); i += 1) {
      const reason = reasonOn(lines[i - 1]);
      if (reason !== null && reason.length >= MIN_WAIVER_REASON_CHARS) return true;
    }
    return false;
  }

  function shortWaiverLine(start, end) {
    for (let i = startOf(start); i <= Math.min(end, lines.length); i += 1) {
      const reason = reasonOn(lines[i - 1]);
      if (reason !== null && reason.length < MIN_WAIVER_REASON_CHARS) return i;
    }
    return null;
  }

  return { waived, shortWaiverLine };
}

function spanTouched(added, start, end) {
  if (added === null) return true;
  for (let i = start; i <= end; i += 1) if (added.has(i)) return true;
  return false;
}

/** A waiver too thin to have been thought about is itself the finding. */
function shortWaiverErrors(marker, filePath, lines, added, waivers, why) {
  const found = [];
  for (let i = 1; i <= lines.length; i += 1) {
    if (!spanTouched(added, i, i)) continue;
    const line = waivers.shortWaiverLine(i, i);
    if (line !== null) {
      found.push(
        `${filePath}:${line}: '${marker}' with no substantive reason — ${why}, in at ` +
          `least ${MIN_WAIVER_REASON_CHARS} characters`,
      );
    }
  }
  return found;
}

module.exports = {
  MIN_WAIVER_REASON_CHARS,
  commentLines,
  waiversFor,
  spanTouched,
  shortWaiverErrors,
};
