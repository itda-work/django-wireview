// Type declarations for external modules and global extensions

declare module "idiomorph" {
  const _load: unknown;
  export default _load;
}

declare const Idiomorph: {
  morph(oldNode: Element, newNode: Element | string): void;
};

interface TransitionConfig {
  transition?: string;
  time?: number;
}

interface JSCommand {
  cmd: string;
  to?: string;
  event?: string;
  value?: Record<string, unknown>;
  target?: string;
  classes?: string;
  attr?: string;
  val?: string;
  url?: string;
  replace?: boolean;
  detail?: Record<string, unknown>;
  bubbles?: boolean;
  display?: string;
  input_only?: boolean;
  transition?: TransitionConfig | string;  // string for standalone transition command
  time?: number;  // for standalone transition command
  show?: TransitionConfig;
  hide?: TransitionConfig;
}

interface Window {
  wireview: {
    send(element: HTMLElement, name: string, args?: Record<string, unknown>, eventType?: string): void;
    debounce(delay: number): <T extends (...args: unknown[]) => void>(f: T) => (...args: Parameters<T>) => void;
    throttle(delay: number): <T extends (...args: unknown[]) => void>(f: T) => (...args: Parameters<T>) => void;
    exec(element: HTMLElement, commands: JSCommand[]): Promise<void>;
  };
}
