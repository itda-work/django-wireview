/**
 * Islands (#221): an element the page's own JavaScript draws -- a React tree,
 * a map, a chart -- fed the component's state.
 *
 * `{% island "Map" props=... %}` renders an element carrying the built-in hook
 * `wireview-island`, the island's name (`wire-island`), its props as JSON
 * (`wire-island-props`), and an inner `wire-update="ignore"` element no render
 * touches. The page registers what draws it:
 *
 *     wireview.islands.Map = {
 *       mount(el, props, ctx) {
 *         // draw into el
 *         return { update(props) {}, unmount() {} };
 *       },
 *     };
 *
 * The hook's life is the island's. `mounted()` mounts it with the props the
 * element carries; each `updated()` -- after every morph of the component --
 * compares the props attribute with the last one handed over and calls
 * `update` once when it changed, never when it did not; `destroyed()` unmounts
 * it. So it lives as long as its element: across a reconnect and a join again
 * (the hook stays, and the render that follows hands over what changed), and
 * across a boosted navigation when its component is sticky. A navigation that
 * takes the element away unmounts it.
 *
 * A definition registered after the element mounted -- a bundle that loads
 * later -- is mounted then (`Islands.registry` is a Proxy that hears it).
 *
 * `ctx` reaches the component: `push(event, payload)` is the hook's
 * `pushEvent` without a callback, a Promise of the reply (#222); `on(event,
 * callback)` is `handleEvent`, returning what removes it; `onStatus(callback)`
 * hears `"disconnected"` and `"reconnected"`, and `status` is
 * `"connected"` or `"disconnected"`.
 *
 * What the island's code throws is reported, not thrown: the page goes on.
 */

/** The client's built-in hook that mounts an island (templatetags' ISLAND_HOOK). */
export const ISLAND_HOOK = "wireview-island";

/**
 * Whether a morph pairs an island's element with the render's: the morph then
 * takes the render's attributes and leaves the children alone (wireview-boost).
 * @param {Node} from
 * @param {Node} to
 * @returns {boolean}
 */
export function isIslandPair(from, to) {
  return (
    from.nodeType === 1 &&
    to.nodeType === 1 &&
    /** @type {Element} */ (from).hasAttribute("wire-island") &&
    /** @type {Element} */ (to).hasAttribute("wire-island")
  );
}

/**
 * Gives `from` the attributes of `to`, as a morph would, and nothing else.
 * @param {Element} from
 * @param {Element} to
 */
export function syncAttributes(from, to) {
  for (const { name } of [...from.attributes]) {
    if (!to.hasAttribute(name)) from.removeAttribute(name);
  }
  for (const { name, value } of [...to.attributes]) {
    if (from.getAttribute(name) !== value) from.setAttribute(name, value);
  }
}

/**
 * @typedef {object} IslandInstance
 * @property {(props: any) => void} [update]
 * @property {() => void} [unmount]
 *
 * @typedef {object} IslandDefinition
 * @property {(el: Element, props: any, ctx: IslandContext) => IslandInstance | void} mount
 *
 * @typedef {object} IslandContext
 * @property {Element} el - the island's outer element
 * @property {Element} root - where the island draws: the element no render touches
 * @property {any} props - the props last handed over
 * @property {"connected" | "disconnected"} status
 * @property {(event: string, payload?: Record<string, unknown>) => Promise<any>} push
 * @property {(event: string, callback: (payload: any) => void) => () => void} on
 * @property {(callback: (status: "disconnected" | "reconnected") => void) => () => void} onStatus
 *
 * @typedef {object} IslandHook - what an island needs of its hook's context
 * @property {Element} el
 * @property {(event: string, payload?: Record<string, unknown>) => Promise<any> | undefined} pushEvent
 * @property {(event: string, callback: (payload: any) => void) => () => void} handleEvent
 */

/** The page's islands: the definitions, and the elements waiting for theirs. */
export class Islands {
  /** @param {{report?: (message: string, error?: unknown) => void}} [options] */
  constructor({ report = (message, error) => console.error(`[wireview] ${message}`, error ?? "") } = {}) {
    this.report = report;
    /** @type {Set<Island>} mounted elements whose definition has not been registered yet */
    this.waiting = new Set();
    /**
     * No prototype: a name like `constructor` is not a definition until assigned.
     * @type {Record<string, IslandDefinition>}
     */
    const definitions = Object.create(null);
    /**
     * `window.wireview.islands`: assigning a name mounts the elements waiting for it.
     * @type {Record<string, IslandDefinition>}
     */
    this.registry = new Proxy(definitions, {
      set: (target, name, definition) => {
        target[/** @type {string} */ (name)] = definition;
        for (const island of [...this.waiting]) {
          if (island.name === name) island.mount();
        }
        return true;
      },
    });
  }
}

/** One island element's life, driven by its hook. */
export class Island {
  /**
   * @param {Islands} islands
   * @param {IslandHook} hook
   */
  constructor(islands, hook) {
    this.islands = islands;
    this.hook = hook;
    this.el = hook.el;
    /** @type {Element} */
    this.root = hook.el.querySelector(':scope > [wire-update="ignore"]') ?? hook.el;
    /** @type {string | null} the name it was mounted under */
    this.mountedAs = null;
    /** @type {IslandInstance} */
    this.instance = {};
    /** @type {string | null} the props attribute last handed over */
    this.handed = null;
    /** @type {any} */
    this.props = undefined;
    /** @type {"connected" | "disconnected"} */
    this.status = "connected";
    /** @type {Set<(status: "disconnected" | "reconnected") => void>} the mounted island's onStatus callbacks */
    this.listeners = new Set();
    /** @type {Array<() => void>} what removes the mounted island's ctx.on handlers */
    this.handlers = [];
    this.gone = false;
    /** @type {IslandContext} the mounted island's; a mount after an unmount gets a new one */
    this.ctx = this.context();
  }

  /** @returns {string} */
  get name() {
    return this.el.getAttribute("wire-island") ?? "";
  }

  /** @returns {IslandContext} */
  context() {
    const island = this;
    return {
      el: island.el,
      root: island.root,
      get props() {
        return island.props;
      },
      get status() {
        return island.status;
      },
      push: (event, payload = {}) => /** @type {Promise<any>} */ (island.hook.pushEvent(event, payload)),
      on: (event, callback) => {
        const off = island.hook.handleEvent(event, callback);
        island.handlers.push(off);
        return off;
      },
      onStatus: (callback) => {
        island.listeners.add(callback);
        return () => island.listeners.delete(callback);
      },
    };
  }

  /**
   * The props the element carries now, and their attribute's text.
   * @returns {{text: string, props: any} | null} null when they are not JSON
   */
  read() {
    const text = this.el.getAttribute("wire-island-props") ?? "{}";
    try {
      return { text, props: JSON.parse(text) };
    } catch (error) {
      this.islands.report(`Island "${this.name}": its props are not JSON`, error);
      return null;
    }
  }

  /** Mounts it, or waits for its definition. */
  mount() {
    if (this.gone || this.mountedAs !== null) return;
    const name = this.name;
    const definition = Object.prototype.hasOwnProperty.call(this.islands.registry, name)
      ? this.islands.registry[name]
      : undefined;
    if (!definition) {
      this.islands.waiting.add(this);
      return;
    }
    this.islands.waiting.delete(this);
    const read = this.read();
    if (read === null) return;
    this.handed = read.text;
    this.props = read.props;
    this.mountedAs = name;
    this.ctx = this.context();
    this.clearRoot();
    try {
      this.instance = definition.mount(this.root, read.props, this.ctx) ?? {};
    } catch (error) {
      this.islands.report(`Island "${name}" threw in mount()`, error);
    }
  }

  /**
   * The element an island draws in, as the tag renders it: empty. A mount
   * finds it so the first time, but not where the page restored a cached copy
   * of itself (a back navigation shows the HTML it left, the old island's
   * drawing in it), nor after an island that left something behind.
   * @private
   */
  clearRoot() {
    const root = this.root;
    if (root === this.el) return;
    for (const { name } of [...root.attributes]) {
      if (name !== "wire-update") root.removeAttribute(name);
    }
    root.replaceChildren();
  }

  /** A morph went over the element: hands over the props if they changed. */
  updated() {
    if (this.gone) return;
    if (this.mountedAs === null) {
      // Waiting for its definition, or its props were not JSON: try again
      this.mount();
      return;
    }
    if (this.name !== this.mountedAs) {
      // Another island under the same element: the old one goes
      this.unmount();
      this.mount();
      return;
    }
    const text = this.el.getAttribute("wire-island-props") ?? "{}";
    if (text === this.handed) return;
    const read = this.read();
    if (read === null) return;
    this.handed = read.text;
    this.props = read.props;
    try {
      this.instance.update?.(read.props);
    } catch (error) {
      this.islands.report(`Island "${this.mountedAs}" threw in update()`, error);
    }
  }

  /**
   * Unmounts the island, and takes back what its ctx subscribed to: the next
   * one under this element does not hear the old one's handlers.
   * @private
   */
  unmount() {
    const name = this.mountedAs;
    this.mountedAs = null;
    this.handed = null;
    const instance = this.instance;
    this.instance = {};
    try {
      instance.unmount?.();
    } catch (error) {
      this.islands.report(`Island "${name}" threw in unmount()`, error);
    }
    for (const off of this.handlers.splice(0)) off();
    this.listeners.clear();
  }

  /** The element left the page. */
  destroy() {
    if (this.gone) return;
    this.gone = true;
    this.islands.waiting.delete(this);
    if (this.mountedAs !== null) this.unmount();
  }

  /** @param {"disconnected" | "reconnected"} status */
  hear(status) {
    if (this.gone) return;
    this.status = status === "disconnected" ? "disconnected" : "connected";
    for (const listener of [...this.listeners]) {
      try {
        listener(status);
      } catch (error) {
        this.islands.report(`Island "${this.name}" threw in an onStatus callback`, error);
      }
    }
  }
}
