import { useEffect, useState } from "react";

import { PrimitiveCard } from "./PrimitiveCard";
import type { GiphyPart } from "./types";

const GIPHY_ID_RE = /^[A-Za-z0-9_-]+$/;

function giphyMediaUrl(part: GiphyPart): string | null {
  if (part.giphy_id && GIPHY_ID_RE.test(part.giphy_id)) {
    return `https://media.giphy.com/media/${encodeURIComponent(part.giphy_id)}/giphy.gif`;
  }
  if (!part.url) return null;
  try {
    const url = new URL(part.url);
    const allowedHost =
      url.hostname === "i.giphy.com" ||
      /^media\d*\.giphy\.com$/.test(url.hostname);
    return url.protocol === "https:" && allowedHost ? url.toString() : null;
  } catch {
    return null;
  }
}

export function GiphyPrimitive({ part }: { part: GiphyPart }) {
  const mediaUrl = giphyMediaUrl(part);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => setLoadFailed(false), [mediaUrl]);

  return (
    <PrimitiveCard
      title={part.title ?? "Giphy"}
      subtitle={part.caption ?? undefined}
      error={
        !mediaUrl
          ? "Provide a valid Giphy ID or HTTPS Giphy media URL"
          : loadFailed
            ? "This Giphy image could not be loaded"
            : null
      }
    >
      {mediaUrl && !loadFailed ? (
        <figure className="lg-primitive-giphy">
          <img
            src={mediaUrl}
            alt={part.alt ?? "Animated GIF"}
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setLoadFailed(true)}
          />
          {part.caption ? <figcaption>{part.caption}</figcaption> : null}
        </figure>
      ) : null}
    </PrimitiveCard>
  );
}
