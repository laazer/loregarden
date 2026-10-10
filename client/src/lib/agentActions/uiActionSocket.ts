/**
 * The tab's side of `/ws/ui-actions`: offer this tab's actions to agents, run
 * the ones they invoke, and answer every invocation — success or failure — so
 * an agent is never left waiting on a tab that quietly dropped its request.
 */

import { describeError } from "../../state/toastStore";
import { ReconnectingSocket, type SocketStatusHandler } from "../reconnectingSocket";
import type { UiActionName } from "./catalog";
import type { UiActionRegistry } from "./registry";

export interface UiActionSocketHandlers extends SocketStatusHandler {
  /** An agent's action has run (or failed) — for telling the operator it happened. */
  onInvoke: (name: UiActionName, outcome: { ok: true } | { ok: false; error: string }) => void;
}

interface InvokeFrame {
  type: "invoke";
  id: string;
  action: UiActionName;
  arguments: Record<string, unknown>;
}

export function uiActionSocketUrl(apiBase: string): string {
  const base = apiBase.replace(/\/$/, "").replace(/^http/, "ws");
  return `${base}/ws/ui-actions`;
}

export class UiActionSocket extends ReconnectingSocket<UiActionSocketHandlers> {
  protected readonly policy = { baseDelayMs: 1000, maxDelayMs: 30000 };
  private registry: UiActionRegistry | null = null;
  private unsubscribe: (() => void) | null = null;

  /** Attach before `open()`: availability is announced on every connect. */
  attach(registry: UiActionRegistry): void {
    this.registry = registry;
    this.unsubscribe = registry.subscribe(() => this.announce());
  }

  /** Whether the operator is looking at this tab — the server prefers one that is. */
  reportVisibility(visible: boolean): void {
    this.send({ type: "focus", visible });
  }

  override close(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    super.close();
  }

  protected override onOpen(): void {
    this.announce();
    this.reportVisibility(globalThis.document?.visibilityState !== "hidden");
  }

  protected handleMessage(raw: unknown): void {
    const frame = raw as Partial<InvokeFrame>;
    if (frame?.type !== "invoke" || typeof frame.id !== "string" || !frame.action) return;
    void this.runInvocation(frame as InvokeFrame);
  }

  private announce(): void {
    // Not connected: nothing to tell; onOpen announces the current set.
    this.send({ type: "register", actions: this.registry?.available() ?? [] });
  }

  private async runInvocation(frame: InvokeFrame): Promise<void> {
    if (!this.registry) {
      this.send({ type: "result", id: frame.id, ok: false, error: "this tab has no action registry" });
      return;
    }
    try {
      const result = await this.registry.run(frame.action, frame.arguments as never);
      this.handlers.onInvoke(frame.action, { ok: true });
      this.send({ type: "result", id: frame.id, ok: true, result: result ?? null });
    } catch (error) {
      const message = describeError(error, `${frame.action} failed in the tab`);
      this.handlers.onInvoke(frame.action, { ok: false, error: message });
      this.send({ type: "result", id: frame.id, ok: false, error: message });
    }
  }
}
