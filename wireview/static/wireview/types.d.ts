// Type declarations for external modules and global extensions

declare module "idiomorph" {
  const _load: unknown;
  export default _load;
}

declare const Idiomorph: {
  morph(oldNode: Element, newNode: Element | string): void;
};

interface Window {
  wireview: {
    send(element: HTMLElement, name: string, args?: Record<string, unknown>): void;
    debounce(delay: number): <T extends (...args: unknown[]) => void>(f: T) => (...args: Parameters<T>) => void;
  };
}
