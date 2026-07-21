// Jest stand-in for @xterm/xterm and @xterm/addon-fit (mapped in jest.config.cjs).
//
// Two reasons, either of which alone would be enough: the package ships ESM
// that this project's CommonJS Jest transform does not process, and xterm
// measures and paints through a canvas that jsdom does not implement.
//
// The protocol logic worth testing lives in src/lib/terminalSocket.ts, which
// has no xterm dependency precisely so it can be tested for real.

export interface Disposable {
  dispose: () => void;
}

export class Terminal {
  rows = 24;
  cols = 80;
  written: string[] = [];
  disposed = false;
  private dataHandler: ((data: string) => void) | null = null;

  loadAddon(): void {}
  open(): void {}

  write(chunk: string): void {
    this.written.push(chunk);
  }

  onData(handler: (data: string) => void): Disposable {
    this.dataHandler = handler;
    return { dispose: () => (this.dataHandler = null) };
  }

  /** Test hook: pretend the operator typed. */
  type(data: string): void {
    this.dataHandler?.(data);
  }

  dispose(): void {
    this.disposed = true;
  }
}

export class FitAddon {
  fit(): void {}
}
