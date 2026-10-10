import { useMediaQuery } from "./useMediaQuery";

/**
 * Whether the reader asked the OS for less motion, kept current as they change it.
 *
 * CSS motion is covered by the global rule in index.css and Motion components by
 * `MotionConfig reducedMotion="user"`; anything that moves from JS
 * (requestAnimationFrame, canvas) must ask this itself.
 */
export function usePrefersReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}
