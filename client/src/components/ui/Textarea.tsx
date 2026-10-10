/**
 * The app's <textarea>. Its colours come from the field floor in index.css; this
 * is the one place a themed textarea will change when light mode lands.
 */

import type { ComponentPropsWithRef } from "react";

export type TextareaProps = ComponentPropsWithRef<"textarea">;

export function Textarea(props: TextareaProps) {
  return <textarea {...props} />;
}
