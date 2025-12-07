import ReconnectingWebSocket from "reconnecting-websocket";
import boost from "./wireview-boost";

/**
 * @fileoverview Wireview client-side library for Django LiveView functionality.
 * Provides WebSocket-based real-time component updates.
 */

// Connection

const parser = new DOMParser();

/**
 * @typedef {Object} WireviewMessage
 * @property {string} command - The command type
 * @property {Object} payload - The message payload
 */

/**
 * @typedef {Object} RenderPayload
 * @property {string} id - Component ID
 * @property {Array<string|number>} diff - Diff array for HTML reconstruction
 */

/**
 * @typedef {Object} DOMPayload
 * @property {string} id - Element ID
 * @property {string} [html] - HTML content
 */

/**
 * Manages WebSocket connection to Django backend.
 * Handles component lifecycle, message routing, and reconnection.
 */
class ServerConnection {
  constructor() {
    /** @type {Object<string, WireviewComponent>} */
    this.components = {};
    /** @type {WireviewMessage[]} */
    this.messageQueue = [];
    /** @type {ReconnectingWebSocket|null} */
    this.socket = null;
  }

  /**
   * Opens WebSocket connection to the server.
   * @param {string} [path="__wireview__"] - WebSocket endpoint path
   */
  open(path = "__wireview__") {
    let protocol = location.protocol.replace("http", "ws");
    this.socket = new ReconnectingWebSocket(
      `${protocol}//${location.host}/${path}`,
      [],
      {
        maxEnqueuedMessages: 0,
      }
    );

    this.socket.addEventListener("open", () => {
      console.log("WS: OPEN");
      this.sendQueryString();
      this.components = {};
      this.joinAllComponents();
      // Flush messages queued while the socket was connecting
      while (this.messageQueue.length) {
        const { command, payload } = this.messageQueue.shift();
        this._send(command, payload);
      }
    });

    this.socket.addEventListener("message", (event) =>
      this._processMessage(event)
    );

    this.socket.addEventListener("close", () => {
      console.log("WS: CLOSE");
      this.components = {};
      document.querySelectorAll("[wireview-component]").forEach((el) => {
        const element = /** @type {HTMLElement} */ (el);
        element.classList.add("wireview-disconnected");
        element.dataset.isLive = "false";
      });
    });

    boost.navEvent.addEventListener("newLocation", () => {
      this.sendQueryString();
    });

    boost.navEvent.addEventListener("newContent", () => {
      this.joinAllComponents();
    });
  }

  /**
   * Whether the WebSocket connection is open.
   * @returns {boolean}
   */
  get isOpen() {
    return this.socket?.readyState == ReconnectingWebSocket.OPEN;
  }

  /**
   * Joins all wireview components found in the DOM.
   * Registers new components and removes stale ones.
   */
  joinAllComponents() {
    let registeredIds = new Set(Object.keys(this.components));
    for (let element of document.querySelectorAll("[wireview-component]")) {
      if (registeredIds.delete(element.id)) {
        this.components[element.id].join();
      } else {
        let component = new WireviewComponent(element.id);
        this.components[element.id] = component;
        component.join();
      }
    }
    for (let id of registeredIds.keys()) {
      delete this.components[id];
      this.sendLeave(id);
    }
  }

  /**
   * Processes incoming WebSocket messages.
   * @param {MessageEvent} event - WebSocket message event
   * @private
   */
  _processMessage(event) {
    let { command, payload } = JSON.parse(event.data);
    switch (command) {
      case "render":
        var { id, diff } = payload;
        console.log("<<< RENDER", id);
        this.components[id]?.applyDiff(diff);
        break;
      case "append":
      case "prepend":
      case "insert_after":
      case "insert_before":
      case "replace_with":
        var { id, html } = payload;
        console.log(`<<< ${command.toUpperCase()}`, id);
        html = parser.parseFromString(html, "text/html").body.firstChild;
        var element = document.getElementById(id);
        if (element) {
          switch (command) {
            case "append":
              element.append(html);
              break;
            case "prepend":
              element.prepend(html);
              break;
            case "insert_after":
              element.after(html);
              break;
            case "insert_before":
              element.before(html);
              break;
            case "replace_with":
              boost.morph(element, html);
              break;
          }
          boost.navEvent.sendNewContent();
        }
        break;
      case "remove":
        var { id } = payload;
        console.log("<<< REMOVE", id);
        document.getElementById(id)?.remove();
        boost.navEvent.sendNewContent();
        break;
      case "focus_on":
        var { selector } = payload;
        console.log(
          "<<< FOCUS-ON",
          `"${selector}"`,
          document.querySelector(selector)
        );
        window.requestAnimationFrame(() =>
          document.querySelector(selector)?.focus()
        );
        break;
      case "scroll_into_view":
        var { id, behavior, block, inline } = payload;
        window.requestAnimationFrame(() =>
          document
            .getElementById(id)
            ?.scrollIntoView({ behavior, block, inline })
        );
        break;
      case "url_change":
        var { url } = payload;
        console.log("<< URL", payload.command, url);
        switch (payload.command) {
          case "redirect":
            boost.HistoryCache.load(url);
            break;
          case "replace":
            boost.HistoryCache.replace(url);
            break;
          case "push":
            boost.HistoryCache.push(url);
            break;
        }
        break;

      case "set_query_string":
        var { qs } = payload;
        qs = qs.length ? `?${qs}` : "";
        console.log("<< SET URL PARAMS", qs);
        boost.HistoryCache.replace(document.location.pathname + qs);
        break;

      case "back":
        boost.HistoryCache.back();
        break;
      default:
        console.error(`Unknown command "${command}"`, payload);
    }
  }

  /**
   * Sends the current query string to the server.
   */
  sendQueryString() {
    // "?a=x&..." -> "a=x&..."
    let qs = document.location.search.slice(1);
    console.log("QS", qs);
    this._send("query_string", { qs });
  }

  /**
   * Sends a join request for a component.
   * @param {string} name - Component class name
   * @param {string} component_id - Component element ID
   * @param {string} state - Serialized component state
   * @param {Object<string, [string, string]>} children - Child component info
   */
  sendJoin(name, component_id, state, children) {
    console.log(">>> JOIN", name, component_id);
    this._send("join", { name, state, children });
  }

  /**
   * Sends a leave request for a component.
   * @param {string} id - Component element ID
   */
  sendLeave(id) {
    console.log(">>> LEAVE", id);
    this._send("leave", { id });
  }

  /**
   * Sends a user event to a component.
   * @param {string} id - Component element ID
   * @param {string} command - Event command name
   * @param {Object} implicit_args - Form data from the component
   * @param {Object} explicit_args - Explicit event arguments
   */
  sendUserEvent(id, command, implicit_args, explicit_args) {
    console.log(">>> USER_EVENT", id, command, explicit_args);
    this._send("user_event", { id, command, implicit_args, explicit_args });
  }

  /**
   * Sends a message to the server.
   * @param {string} command - Command type
   * @param {Object} payload - Message payload
   * @private
   */
  _send(command, payload) {
    const message = { command, payload };
    if (this.isOpen) {
      this.socket.send(JSON.stringify(message));
    } else {
      this.messageQueue.push(message);
    }
  }
}

/** @type {ServerConnection} */
let connection = new ServerConnection();

/**
 * Represents a client-side wireview component.
 * Manages state synchronization with the server.
 */
class WireviewComponent {
  /**
   * Creates a new WireviewComponent instance.
   * @param {string} id - The DOM element ID for this component
   */
  constructor(id) {
    /** @type {string} */
    this.id = id;
    /** @type {string[]} */
    this.lastReceivedHtml = [];
  }

  /**
   * Gets the DOM element for this component.
   * @returns {HTMLElement|null}
   */
  getElemenet() {
    return document.getElementById(this.id);
  }

  /**
   * Applies a diff from the server to update the component's HTML.
   * @param {Array<string|number>} diff - Diff array for HTML reconstruction
   */
  applyDiff(diff) {
    window.requestAnimationFrame(() => {
      let el = this.getElemenet();
      if (el) {
        // Remove loading classes before morphing
        this.clearLoadingClasses();

        let html = this.getHtml(diff);
        boost.morph(el, html);
        boost.navEvent.sendNewContent();
      }
    });
  }

  /**
   * Removes all loading classes from elements within this component.
   */
  clearLoadingClasses() {
    const el = this.getElemenet();
    if (!el) return;

    const loadingElements = el.querySelectorAll(".wireview-loading");
    for (const loadingEl of loadingElements) {
      loadingEl.classList.remove(
        "wireview-loading",
        "wireview-click-loading",
        "wireview-submit-loading",
        "wireview-change-loading",
        "wireview-input-loading",
        "wireview-keydown-loading",
        "wireview-keyup-loading"
      );
    }
  }

  /**
   * Reconstructs HTML from a diff array.
   * @param {Array<string|number>} diff - Diff array where:
   *   - string: new content to add
   *   - negative number: skip that many fragments from lastReceivedHtml
   *   - positive number: reuse that many fragments from lastReceivedHtml
   * @returns {string} Reconstructed HTML string
   */
  getHtml(diff) {
    let fragments = [];
    let cursor = 0;
    for (let fragment of diff) {
      if (typeof fragment === "string") {
        fragments.push(fragment);
      } else if (fragment < 0) {
        cursor -= fragment;
      } else {
        fragments.push(
          ...this.lastReceivedHtml.slice(cursor, cursor + fragment)
        );
        cursor += fragment;
      }
    }
    this.lastReceivedHtml = fragments;
    return fragments.join(" ");
  }

  /**
   * Joins this component to the server.
   * Only joins if the component is not already live and its parent is live.
   */
  join() {
    const element = /** @type {HTMLElement|null} */ (this.getElemenet());
    if (element && element.dataset.isLive === "false") {
      const parentEl = element?.parentElement?.closest("[wireview-component]");
      const parent = /** @type {HTMLElement|null} */ (parentEl);
      if (!parent || parent.dataset.isLive === "true") {
        element.dataset.isLive = "true";
        /** @type {Object<string, [string, string]>} */
        let children = Array.from(
          element.querySelectorAll("[wireview-component]")
        ).reduce((acc, node) => {
          const el = /** @type {HTMLElement} */ (node);
          acc[el.id] = [el.dataset.name || "", el.dataset.state || ""];
          return acc;
        }, /** @type {Object<string, [string, string]>} */ ({}));

        connection.sendJoin(
          element.dataset.name || "",
          element.id,
          element.dataset.state || "",
          children
        );
      }
    }
  }

  /**
   * Dispatches a command to this component and sends it to the backend
   * @param {string} command - Event command name
   * @param {Object} args - Explicit arguments
   * @param {HTMLElement} formScope - Form or component element to serialize
   */
  dispatch(command, args, formScope) {
    connection.sendUserEvent(this.id, command, this.serialize(formScope), args);
  }

  /**
   * Serialize all elements inside `element` with a [name] attribute into
   * a an array of `[element[name], element[value]]`
   * @param {HTMLElement} element
   * @returns {Object<string, Array<string|boolean>>}
   */
  serialize(element) {
    /** @type {Object<string, Array<string|boolean>>} */
    let result = {};
    let thisElement = this.getElemenet();
    for (let node of element.querySelectorAll("[name]")) {
      const el = /** @type {HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement} */ (node);
      // Avoid serializing data of a nested component
      if (el.closest("[wireview-component]") !== thisElement) {
        continue;
      }

      /** @type {string|boolean|string[]|null} */
      let value = null;
      const elType = el.type?.toLowerCase() || "";
      switch (elType) {
        case "checkbox":
        case "radio":
          value = /** @type {HTMLInputElement} */ (el).checked
            ? el.value || true
            : null;
          break;
        case "select-multiple":
          value = Array.from(/** @type {HTMLSelectElement} */ (el).selectedOptions).map(
            (option) => option.value
          );
          break;
        default:
          value = el.value;
          break;
      }

      if (value !== null) {
        let key = el.getAttribute("name");
        if (key) {
          let values = result[key] ?? [];
          values.push(/** @type {string|boolean} */ (value));
          result[key] = values;
        }
      }
    }
    return result;
  }
}

connection.open();
/** @type {ReturnType<typeof setTimeout>|undefined} */
var debounceTimeout = undefined;
/** @type {number} */
var throttleLastCall = 0;

// ============================================================================
// JS Command Types and Executor
// ============================================================================

/**
 * @typedef {Object} JSCommand
 * @property {string} cmd - Command name
 * @property {string} [to] - Target selector
 * @property {string} [event] - Event name for push/dispatch
 * @property {Object} [value] - Data for push command
 * @property {string} [target] - Target component for push
 * @property {string} [classes] - CSS classes to add/remove
 * @property {string} [attr] - Attribute name
 * @property {string} [val] - Attribute value
 * @property {string} [url] - URL for navigation
 * @property {boolean} [replace] - Replace history for navigate
 * @property {Object} [detail] - Detail for dispatch event
 * @property {boolean} [bubbles] - Whether dispatch event bubbles
 * @property {string} [display] - CSS display value for show
 * @property {boolean} [input_only] - Focus only inputs
 * @property {*} [transition] - Transition config (object) or class names (string)
 * @property {number} [time] - Transition duration in ms (for transition command)
 * @property {TransitionConfig} [show] - Show transition for toggle
 * @property {TransitionConfig} [hide] - Hide transition for toggle
 */

/**
 * @typedef {Object} TransitionConfig
 * @property {string} [transition] - CSS class(es) to apply
 * @property {number} [time] - Transition duration in ms
 */

/**
 * Resolves target element(s) from a selector.
 * @param {string|null|undefined} selector - CSS selector or null
 * @param {HTMLElement} currentElement - Fallback element
 * @returns {HTMLElement|null}
 */
function resolveTarget(selector, currentElement) {
  if (!selector) return currentElement;
  return document.querySelector(selector);
}

/**
 * Applies a CSS transition to an element.
 * @param {HTMLElement} element - Target element
 * @param {TransitionConfig|null|undefined} config - Transition config
 * @returns {Promise<void>}
 */
async function applyTransition(element, config) {
  if (!config || !config.transition) return;

  const classes = config.transition.split(" ").filter(Boolean);
  element.classList.add(...classes);

  if (config.time && config.time > 0) {
    await new Promise((resolve) => setTimeout(resolve, config.time));
    element.classList.remove(...classes);
  }
}

/**
 * Executes a single JS command.
 * @param {JSCommand} cmd - Command to execute
 * @param {HTMLElement} element - Context element (event target)
 * @returns {Promise<void>}
 */
async function executeCommand(cmd, element) {
  const target = resolveTarget(cmd.to, element);

  switch (cmd.cmd) {
    // Visibility commands
    case "show":
      if (target) {
        target.style.display = cmd.display || "";
        target.hidden = false;
        await applyTransition(target, cmd.transition);
      }
      break;

    case "hide":
      if (target) {
        await applyTransition(target, cmd.transition);
        target.hidden = true;
      }
      break;

    case "toggle":
      if (target) {
        if (target.hidden) {
          target.style.display = cmd.display || "";
          target.hidden = false;
          await applyTransition(target, cmd.show);
        } else {
          await applyTransition(target, cmd.hide);
          target.hidden = true;
        }
      }
      break;

    // CSS class commands
    case "add_class":
      if (target && cmd.classes) {
        const classes = cmd.classes.split(" ").filter(Boolean);
        await applyTransition(target, cmd.transition);
        target.classList.add(...classes);
      }
      break;

    case "remove_class":
      if (target && cmd.classes) {
        const classes = cmd.classes.split(" ").filter(Boolean);
        await applyTransition(target, cmd.transition);
        target.classList.remove(...classes);
      }
      break;

    case "toggle_class":
      if (target && cmd.classes) {
        const classes = cmd.classes.split(" ").filter(Boolean);
        await applyTransition(target, cmd.transition);
        classes.forEach((cls) => target.classList.toggle(cls));
      }
      break;

    // Transition command
    case "transition":
      if (target && cmd.transition) {
        // For transition command, cmd.transition is always a string (class names)
        const transitionClasses = /** @type {string} */ (cmd.transition);
        const classes = transitionClasses.split(" ").filter(Boolean);
        target.classList.add(...classes);
        const duration = /** @type {number|undefined} */ (cmd.time);
        if (duration && duration > 0) {
          await new Promise((resolve) => setTimeout(resolve, duration));
          target.classList.remove(...classes);
        }
      }
      break;

    // Attribute commands
    case "set_attr":
      if (target && cmd.attr) {
        target.setAttribute(cmd.attr, cmd.val || "");
      }
      break;

    case "remove_attr":
      if (target && cmd.attr) {
        target.removeAttribute(cmd.attr);
      }
      break;

    // Focus commands
    case "focus":
      if (target) {
        window.requestAnimationFrame(() => target.focus());
      }
      break;

    case "focus_first":
      if (target) {
        const selector = cmd.input_only
          ? "input:not([disabled]):not([type=hidden]), textarea:not([disabled])"
          : "input:not([disabled]):not([type=hidden]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex='-1'])";
        const firstFocusable = /** @type {HTMLElement|null} */ (
          target.querySelector(selector)
        );
        if (firstFocusable) {
          window.requestAnimationFrame(() => firstFocusable.focus());
        }
      }
      break;

    // Server communication
    case "push":
      if (cmd.event) {
        // Resolve the component to push to
        const pushTarget = cmd.target
          ? document.querySelector(cmd.target)
          : element.closest("[wireview-component]");
        const componentEl = /** @type {HTMLElement|null} */ (pushTarget);

        if (componentEl) {
          const component = connection.components[componentEl.id];
          if (component) {
            const form = /** @type {HTMLFormElement|null} */ (
              element.closest("form")
            );
            const formScope =
              form && componentEl.contains(form) ? form : componentEl;
            component.dispatch(cmd.event, cmd.value || {}, formScope);
          }
        }
      }
      break;

    // Browser commands
    case "navigate":
      if (cmd.url) {
        if (cmd.replace) {
          boost.HistoryCache.replace(cmd.url);
        } else {
          boost.HistoryCache.load(cmd.url);
        }
      }
      break;

    case "dispatch":
      if (cmd.event) {
        const dispatchTarget = resolveTarget(cmd.to, element);
        if (dispatchTarget) {
          const customEvent = new CustomEvent(cmd.event, {
            detail: cmd.detail || {},
            bubbles: cmd.bubbles !== false,
            cancelable: true,
          });
          dispatchTarget.dispatchEvent(customEvent);
        }
      }
      break;

    default:
      console.warn(`Unknown JS command: ${cmd.cmd}`, cmd);
  }
}

window.wireview = {
  /**
   * Forwards a user event to a component
   * @param {HTMLElement} element
   * @param {string} name
   * @param {Object} [args]
   * @param {string} [eventType] - Optional event type for loading class
   */
  send(element, name, args, eventType) {
    const component_el = /** @type {HTMLElement|null} */ (
      element.closest("[wireview-component]")
    );
    if (component_el === null) return;
    let component = connection.components[component_el.id];
    if (component !== undefined) {
      // Add loading classes
      element.classList.add("wireview-loading");
      if (eventType) {
        element.classList.add(`wireview-${eventType}-loading`);
      }

      const form = /** @type {HTMLFormElement|null} */ (element.closest("form"));
      const formScope = form && component_el.contains(form) ? form : component_el;
      component.dispatch(name, args || {}, formScope);
    }
  },

  /**
   * Debounce a function call
   * @param {number} delay - Delay in milliseconds
   * @returns {<T extends (...args: any[]) => void>(f: T) => (...args: Parameters<T>) => void}
   */
  debounce(delay) {
    return (/** @type {Function} */ f) => {
      return (/** @type {any[]} */ ...args) => {
        clearTimeout(debounceTimeout);
        debounceTimeout = setTimeout(() => f(...args), delay);
      };
    };
  },

  /**
   * Throttle a function call (execute at most once per delay period)
   * @param {number} delay - Minimum time between calls in milliseconds
   * @returns {<T extends (...args: any[]) => void>(f: T) => (...args: Parameters<T>) => void}
   */
  throttle(delay) {
    return (/** @type {Function} */ f) => {
      return (/** @type {any[]} */ ...args) => {
        const now = Date.now();
        if (now - throttleLastCall >= delay) {
          throttleLastCall = now;
          f(...args);
        }
      };
    };
  },

  /**
   * Execute an array of JS commands.
   * Commands are executed sequentially in the order provided.
   * @param {HTMLElement} element - Context element (typically event.target)
   * @param {JSCommand[]} commands - Array of commands to execute
   * @returns {Promise<void>}
   */
  async exec(element, commands) {
    for (const cmd of commands) {
      await executeCommand(cmd, element);
    }
  },
};
