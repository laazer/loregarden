/**
 * WebSocket client service for real-time parallel execution updates.
 * Manages Socket.IO connection, auto-reconnection, and event handling.
 */

import { io, Socket } from 'socket.io-client';

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'error';

export interface WebSocketEvent {
  type: string;
  timestamp: string;
  data?: Record<string, any>;
}

export interface ConnectionStatus {
  state: ConnectionState;
  connected: boolean;
  reconnecting: boolean;
  error?: string;
  lastUpdate: string;
}

class WebSocketClient {
  private socket: Socket | null = null;
  private connectionState: ConnectionState = 'disconnected';
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private baseDelay = 1000; // 1 second
  private maxDelay = 30000; // 30 seconds
  private eventHandlers: Map<string, Set<(data: any) => void>> = new Map();
  private subscriptions: Set<string> = new Set();
  private serverUrl: string;

  constructor(serverUrl: string = '') {
    this.serverUrl = serverUrl;
    // Use window.location.origin for browser environment
    if (!serverUrl && typeof window !== 'undefined') {
      this.serverUrl = window.location.origin;
    }
  }

  /**
   * Connect to WebSocket server.
   */
  connect(userId?: string): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.socket?.connected) {
        resolve();
        return;
      }

      this.updateState('connecting');

      const query: Record<string, string> = {};
      if (userId) {
        query.user_id = userId;
      }

      this.socket = io(this.serverUrl, {
        reconnection: true,
        reconnectionDelay: this.baseDelay,
        reconnectionDelayMax: this.maxDelay,
        reconnectionAttempts: this.maxReconnectAttempts,
        transports: ['websocket', 'polling'],
        query,
      });

      this.socket.on('connect', () => {
        this.reconnectAttempts = 0;
        this.updateState('connected');
        this.emit('websocket:connected', {});
        resolve();
      });

      this.socket.on('disconnect', (reason) => {
        this.updateState('disconnected');
        this.emit('websocket:disconnected', { reason });
      });

      this.socket.on('connect_error', (error) => {
        this.updateState('error');
        this.emit('websocket:error', { message: error.message });
        reject(error);
      });

      this.socket.on('reconnect_attempt', () => {
        this.reconnectAttempts++;
        this.updateState('connecting');
      });

      this.socket.on('reconnect_failed', () => {
        this.updateState('error');
        this.emit('websocket:reconnect_failed', {});
      });

      // Register global event handlers
      this.registerGlobalHandlers();
    });
  }

  /**
   * Disconnect from WebSocket server.
   */
  disconnect(): void {
    if (this.socket) {
      this.socket.disconnect();
      this.socket = null;
      this.subscriptions.clear();
      this.updateState('disconnected');
    }
  }

  /**
   * Check if currently connected.
   */
  isConnected(): boolean {
    return this.connectionState === 'connected' && this.socket?.connected === true;
  }

  /**
   * Get current connection state.
   */
  getState(): ConnectionState {
    return this.connectionState;
  }

  /**
   * Get connection status.
   */
  getStatus(): ConnectionStatus {
    return {
      state: this.connectionState,
      connected: this.isConnected(),
      reconnecting: this.connectionState === 'connecting' && this.reconnectAttempts > 0,
      error: this.connectionState === 'error' ? 'Connection failed' : undefined,
      lastUpdate: new Date().toISOString(),
    };
  }

  /**
   * Subscribe to workspace execution updates.
   */
  joinWorkspace(workspaceId: string): void {
    if (!this.isConnected()) {
      console.warn('WebSocket not connected. Cannot join workspace.');
      return;
    }

    const room = `workspace:${workspaceId}`;
    if (!this.subscriptions.has(room)) {
      this.socket?.emit('join_workspace', {
        workspaceId,
        timestamp: new Date().toISOString(),
      });
      this.subscriptions.add(room);
    }
  }

  /**
   * Unsubscribe from workspace execution updates.
   */
  leaveWorkspace(workspaceId: string): void {
    if (!this.socket) return;

    const room = `workspace:${workspaceId}`;
    if (this.subscriptions.has(room)) {
      this.socket.emit('leave_workspace', {
        workspaceId,
        timestamp: new Date().toISOString(),
      });
      this.subscriptions.delete(room);
    }
  }

  /**
   * Subscribe to worktree conflict updates.
   */
  joinWorktree(worktreeId: string, runId?: string): void {
    if (!this.isConnected()) {
      console.warn('WebSocket not connected. Cannot join worktree.');
      return;
    }

    const room = `worktree:${worktreeId}`;
    if (!this.subscriptions.has(room)) {
      this.socket?.emit('join_worktree', {
        worktreeId,
        runId,
        timestamp: new Date().toISOString(),
      });
      this.subscriptions.add(room);
    }
  }

  /**
   * Unsubscribe from worktree conflict updates.
   */
  leaveWorktree(worktreeId: string): void {
    if (!this.socket) return;

    const room = `worktree:${worktreeId}`;
    if (this.subscriptions.has(room)) {
      this.socket.emit('leave_worktree', {
        worktreeId,
        timestamp: new Date().toISOString(),
      });
      this.subscriptions.delete(room);
    }
  }

  /**
   * Request to cancel a run.
   */
  cancelRun(runId: string, reason: string = 'user_request'): void {
    if (!this.isConnected()) {
      console.warn('WebSocket not connected. Cannot cancel run.');
      return;
    }

    this.socket?.emit('cancel_run', {
      runId,
      reason,
      timestamp: new Date().toISOString(),
    });
  }

  /**
   * Request to resolve conflicts.
   */
  resolveConflicts(worktreeId: string, strategy: string = 'auto'): void {
    if (!this.isConnected()) {
      console.warn('WebSocket not connected. Cannot resolve conflicts.');
      return;
    }

    this.socket?.emit('resolve_conflicts', {
      worktreeId,
      strategy,
      timestamp: new Date().toISOString(),
    });
  }

  /**
   * Register a handler for a WebSocket event.
   */
  on(event: string, handler: (data: any) => void): void {
    if (!this.eventHandlers.has(event)) {
      this.eventHandlers.set(event, new Set());
    }
    this.eventHandlers.get(event)!.add(handler);

    // Also register with Socket.IO if socket exists
    if (this.socket) {
      this.socket.on(event, handler);
    }
  }

  /**
   * Unregister a handler for a WebSocket event.
   */
  off(event: string, handler: (data: any) => void): void {
    const handlers = this.eventHandlers.get(event);
    if (handlers) {
      handlers.delete(handler);
    }

    if (this.socket) {
      this.socket.off(event, handler);
    }
  }

  /**
   * Listen for a one-time WebSocket event.
   */
  once(event: string, handler: (data: any) => void): void {
    if (this.socket) {
      this.socket.once(event, handler);
    }
  }

  /**
   * Get subscribed rooms.
   */
  getSubscriptions(): string[] {
    return Array.from(this.subscriptions);
  }

  /**
   * Manually reconnect to server.
   */
  reconnect(): void {
    if (this.socket) {
      this.reconnectAttempts = 0;
      this.socket.connect();
    }
  }

  /**
   * Private: Update connection state and notify listeners.
   */
  private updateState(newState: ConnectionState): void {
    this.connectionState = newState;
    this.emit('websocket:state_change', { state: newState });
  }

  /**
   * Private: Emit internal events.
   */
  private emit(event: string, data: any): void {
    const handlers = this.eventHandlers.get(event);
    if (handlers) {
      handlers.forEach((handler) => handler(data));
    }
  }

  /**
   * Private: Register global event handlers.
   */
  private registerGlobalHandlers(): void {
    if (!this.socket) return;

    // Server broadcast events
    this.socket.on('execution_update', (data) => {
      this.emit('execution_update', data);
    });

    this.socket.on('conflict_detected', (data) => {
      this.emit('conflict_detected', data);
    });

    this.socket.on('conflict_resolved', (data) => {
      this.emit('conflict_resolved', data);
    });

    this.socket.on('queue_promoted', (data) => {
      this.emit('queue_promoted', data);
    });

    this.socket.on('run_completed', (data) => {
      this.emit('run_completed', data);
    });

    this.socket.on('error', (data) => {
      this.emit('websocket:server_error', data);
    });
  }
}

// Singleton instance
let instance: WebSocketClient | null = null;

/**
 * Get or create WebSocket client singleton.
 */
export function getWebSocketClient(serverUrl?: string): WebSocketClient {
  if (!instance) {
    instance = new WebSocketClient(serverUrl);
  }
  return instance;
}

/**
 * Reset WebSocket client (for testing).
 */
export function resetWebSocketClient(): void {
  if (instance) {
    instance.disconnect();
    instance = null;
  }
}
