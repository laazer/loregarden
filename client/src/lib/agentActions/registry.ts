/**
 * Which UI actions this tab can perform right now, and the handler for each.
 *
 * The most recently mounted registration answers first, and unmounting it
 * restores the one before. A control that appears once per item — a card per
 * workspace, a composer per run — registers once per instance and throws
 * `NotThisTarget` for an item that is not its own; the next registration is
 * then asked, so the request reaches the instance it names. Listeners hear
 * about every change to the set of available names, which is what the socket
 * tells the server.
 */

import type { UiActionArgs, UiActionHandler, UiActionName } from "./catalog";

/** "Not mine — ask the next one." Any other throw is a refusal and stops there. */
export class NotThisTarget extends Error {}

type AnyHandler = (args: never) => Promise<unknown>;

export class UiActionRegistry {
  private readonly stacks = new Map<UiActionName, AnyHandler[]>();
  private readonly listeners = new Set<() => void>();

  register<N extends UiActionName>(name: N, handler: UiActionHandler<N>): () => void {
    const stack = this.stacks.get(name) ?? [];
    const entry = handler as AnyHandler;
    stack.push(entry);
    this.stacks.set(name, stack);
    this.changed();
    return () => {
      const current = this.stacks.get(name);
      if (!current) return;
      const at = current.lastIndexOf(entry);
      if (at !== -1) current.splice(at, 1);
      if (current.length === 0) this.stacks.delete(name);
      this.changed();
    };
  }

  available(): UiActionName[] {
    return [...this.stacks.keys()].sort();
  }

  /** Run an action. Throws when nothing offers it, so the agent hears why. */
  async run<N extends UiActionName>(name: N, args: UiActionArgs[N]): Promise<unknown> {
    const stack = [...(this.stacks.get(name) ?? [])].reverse() as UiActionHandler<N>[];
    if (stack.length === 0) throw new Error(`this tab no longer offers ${name}`);
    let declined: NotThisTarget | null = null;
    for (const handler of stack) {
      try {
        return await handler(args);
      } catch (error) {
        // ts-org: allow-instanceof — dispatch on our own sentinel class, not error narrowing for display
        if (!(error instanceof NotThisTarget)) throw error;
        declined = error;
      }
    }
    // Every instance said "not mine": the target is not on screen. Say so with
    // the last instance's own words, which name what it does hold.
    throw new Error(declined?.message ?? `nothing on screen matches this ${name} request`);
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private changed(): void {
    for (const listener of this.listeners) listener();
  }
}

/** The tab's one registry: the socket and every `useAgentAction` meet here. */
export const uiActionRegistry = new UiActionRegistry();
