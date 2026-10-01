/**
 * The app's <select>. Its colours come from the field floor in index.css, and
 * its dropdown from `color-scheme` on :root; this is the one place a themed
 * select will change when light mode lands.
 */

import type { ComponentPropsWithRef } from "react";

export type SelectProps = ComponentPropsWithRef<"select">;

export function Select(props: SelectProps) {
  return <select {...props} />;
}
