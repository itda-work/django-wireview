// The few pieces of the VS Code API that src/folders.ts reaches for, so that its
// lifecycle runs under node: test/folders.test.ts maps the "vscode" import here.
// What the editor would decide (the settings, the trust) is in `state`.

export interface Disposable {
  dispose(): void;
}

export class Watcher implements Disposable {
  readonly pattern: RelativePattern;
  disposed = false;
  private readonly handlers: (() => void)[] = [];

  constructor(pattern: RelativePattern) {
    this.pattern = pattern;
  }

  onDidChange(handler: () => void): Disposable {
    this.handlers.push(handler);
    return { dispose() {} };
  }

  onDidCreate(handler: () => void): Disposable {
    this.handlers.push(handler);
    return { dispose() {} };
  }

  /** The file changed on disk. A disposed watcher hears nothing, as in the editor. */
  fire(): void {
    if (!this.disposed) for (const handler of this.handlers) handler();
  }

  /** What a handler would do if the editor delivered an event already in flight. */
  fireAnyway(): void {
    for (const handler of this.handlers) handler();
  }

  dispose(): void {
    this.disposed = true;
  }
}

export class RelativePattern {
  readonly base: unknown;
  readonly pattern: string;

  constructor(base: unknown, pattern: string) {
    this.base = base;
    this.pattern = pattern;
  }
}

export const state = {
  config: {} as Record<string, unknown>,
  trusted: true,
  watchers: [] as Watcher[],
};

export const workspace = {
  getConfiguration: () => ({ get: <T>(key: string, fallback: T): T => (state.config[key] as T | undefined) ?? fallback }),
  findFiles: async () => [],
  createFileSystemWatcher(pattern: RelativePattern): Watcher {
    const watcher = new Watcher(pattern);
    state.watchers.push(watcher);
    return watcher;
  },
  get isTrusted(): boolean {
    return state.trusted;
  },
};

export const extensions = { getExtension: () => undefined };
