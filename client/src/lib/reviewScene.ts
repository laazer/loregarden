import type { ReviewItem } from "../api/reviewApi";

/** A scene's file name without its folder or suffix, as the reviewer knows it. */
export function sceneName(scene: string): string {
  const file = scene.slice(scene.lastIndexOf("/") + 1);
  return file.replace(/\.(tscn|scn)$/, "");
}

/**
 * The scene a ticket is checked in: the one its kit names, else the only scene
 * it changes. Several changed scenes are a choice for the reviewer, not a guess.
 */
export function checkedScene(item: ReviewItem): string {
  if (item.kit.scene) return item.kit.scene;
  return item.scenes?.length === 1 ? item.scenes[0] : "";
}
