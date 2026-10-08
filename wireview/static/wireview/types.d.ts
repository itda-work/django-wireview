// Type declarations for external modules and global extensions

declare module "idiomorph" {
  export const Idiomorph: {
    morph(oldNode: Element, newNode: Element | string): void;
  };
}

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

interface WireviewDebug {
  enable(): void;
  disable(): void;
  latency(ms: number): void;
  status(): void;
  components(): Record<string, unknown>;
  component(id: string): unknown;
}

/** What started a boosted move (docs/features/boost.md, #154). */
type WireviewNavigationKind = "link" | "form" | "visit" | "push" | "replace" | "redirect" | "popstate";

/**
 * `wireview:before-navigate`: a boosted move is about to happen, and
 * `preventDefault()` keeps the page where it is (docs/features/boost.md, #154).
 * A `redirect` is not cancelable, nor is a popstate where the browser has no Navigation API.
 */
interface WireviewBeforeNavigateDetail {
  /** Where it goes, resolved. For a popstate, the entry the address bar names already. */
  url: string;
  kind: WireviewNavigationKind;
  /** The page stays and only its params change: nothing is fetched. */
  patch: boolean;
  /** The form being sent, for `kind: "form"`. */
  form?: HTMLFormElement;
}

/** `wireview:navigated`: a boosted navigation landed (docs/features/boost.md, #128). */
interface WireviewNavigatedDetail {
  /** Where the navigation ended, after any redirect. */
  url: string;
  /** The page it left. */
  previousUrl: string;
  /** What started it (#154). */
  kind: WireviewNavigationKind;
}

/** `wireview:navigation-failed`: a boosted form submission put no page on screen (docs/features/boost.md, #170). */
interface WireviewNavigationFailedDetail {
  /** The form's action. */
  url: string;
  /** The method it was sent with, upper case. */
  method: string;
  /**
   * False: no answer, the network failed, and the form may or may not have
   * reached the server. True: the server took it and redirected to another
   * origin, which a boosted request cannot follow.
   */
  answered: boolean;
}

interface DocumentEventMap {
  "wireview:before-navigate": CustomEvent<WireviewBeforeNavigateDetail>;
  "wireview:navigated": CustomEvent<WireviewNavigatedDetail>;
  "wireview:navigation-failed": CustomEvent<WireviewNavigationFailedDetail>;
}

/** What `window.wireview.hooks.<Name>` may define (docs/features/hooks.md). `this` is the hook's context. */
interface WireviewHook {
  mounted?(): void;
  beforeUpdate?(): void;
  updated?(): void;
  destroyed?(): void;
  disconnected?(): void;
  reconnected?(): void;
  /** After a boosted navigation the hook stayed on the page through: a sticky component's (#128). */
  navigated?(): void;
}

interface Window {
  wireview: {
    /**
     * Go to a URL as a boosted link does (in place under `BOOST_PAGES`, otherwise a page load).
     * Resolves to false when a full page load took over, or `wireview:before-navigate` was cancelled.
     */
    visit(url: string, options?: { replace?: boolean }): Promise<boolean>;
    /**
     * `options.commit`: the event commits the fields it comes from, so its answer may reset
     * them (docs/features/html-diff.md, "입력 중인 값"). By default decided from `eventType`.
     */
    send(
      element: HTMLElement,
      name: string,
      args?: Record<string, unknown>,
      options?: { eventType?: string; commit?: boolean; target?: string },
    ): void;
    debug: WireviewDebug;
    hooks: Record<string, WireviewHook & ThisType<any>>;
  };
}
