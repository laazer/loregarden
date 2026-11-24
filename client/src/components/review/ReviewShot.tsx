import { useState } from "react";

import { Button } from "../ui/Button";

/**
 * One screenshot an agent captured. Its file lives beside the database rather
 * than in it, so it can be gone while the record remains; that is said in the
 * image's place, since a broken image reads as a page that did not load.
 */
export function ReviewShot({ src, caption }: { src: string; caption: string }) {
  const [missing, setMissing] = useState(false);
  return (
    <li>
      {missing ? (
        <div className="review-shot-missing" role="status">
          <p>The screenshot&rsquo;s file did not load. If it stays missing, ask the agent to capture it again.</p>
          <Button variant="plain" className="tab-btn" onClick={() => setMissing(false)}>
            Try again
          </Button>
        </div>
      ) : (
        <a
          href={src}
          target="_blank"
          rel="noreferrer"
          aria-label={`Open full size: ${caption || "screenshot"}`}
        >
          <img
            src={src}
            alt={caption || "Screenshot the agent captured"}
            loading="lazy"
            onError={() => setMissing(true)}
          />
        </a>
      )}
      {caption ? <span className="review-muted">{caption}</span> : null}
    </li>
  );
}
