/**
 * Which UI actions this tab can perform right now, and the handler for each.
 *
 * One handler per action: the most recently mounted registration wins, and
 * unmounting it restores the one before (two ticket views stacked in a pane
 * hand the action back and forth correctly). Listeners hear about every change
 * to the set of available names, which is what the socket tells the server.
 */

import type { UiActionArgs, UiActionHandler, UiActionName } from "./catalog";

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
    const stack = this.stacks.get(name);
    const handler = stack?.[stack.length - 1] as UiActionHandler<N> | undefined;
    if (!handler) throw new Error(`this tab no longer offers ${name}`);
    return handler(args);
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
