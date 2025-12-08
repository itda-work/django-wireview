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
 * @property {Array<string|number>|Object} diff - Diff array (legacy) or object (Phoenix-style)
 */

/**
 * @typedef {Object} PhoenixFullDiff
 * @property {string[]} s - Static parts
 * @property {string[]} d - Dynamic parts
 * @property {string} f - Fingerprint
 */

/**
 * @typedef {Object<string, string>} PhoenixPartialDiff
 * Partial diff with numeric string keys mapping to new values
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
      debugLog("ws", "Connected to server");
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
      debugLog("ws", "Disconnected from server");
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
    debugLog("recv", command, payload);
    switch (command) {
      case "render":
        var { id, diff } = payload;
        this.components[id]?.applyDiff(diff);
        break;
      case "append":
      case "prepend":
      case "insert_after":
      case "insert_before":
      case "replace_with":
        var { id, html } = payload;
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
        document.getElementById(id)?.remove();
        boost.navEvent.sendNewContent();
        break;
      case "focus_on":
        var { selector } = payload;
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
        boost.HistoryCache.replace(document.location.pathname + qs);
        break;

      case "back":
        boost.HistoryCache.back();
        break;

      case "stream_op":
        var { op, stream, items, at } = payload;
        this._handleStreamOp(op, stream, items, at);
        break;

      default:
        console.warn(`[wireview] Unknown command "${command}"`, payload);
    }
  }

  /**
   * Handle stream operations (reset, insert, delete).
   * @param {string} op - Operation type ("reset", "insert", "delete")
   * @param {string} stream - Stream name (matches wire-stream attribute)
   * @param {Array<{id: string, html: string}>} items - Stream items
   * @param {number} at - Insert position (-1 = append, 0 = prepend)
   * @private
   */
  _handleStreamOp(op, stream, items, at) {
    const container = document.querySelector(`[wire-stream="${stream}"]`);
    if (!container) {
      console.warn(`[wireview] Stream container not found: ${stream}`);
      return;
    }

    switch (op) {
      case "reset":
        // Clear container and add all items
        container.innerHTML = "";
        for (const item of items) {
          const el = this._parseHtml(item.html);
          if (el) {
            el.id = item.id;
            container.appendChild(el);
          }
        }
        break;

      case "insert":
        for (const item of items) {
          const el = this._parseHtml(item.html);
          if (el) {
            el.id = item.id;
            if (at === 0) {
              // Prepend
              container.prepend(el);
            } else if (at === -1 || at >= container.children.length) {
              // Append
              container.appendChild(el);
            } else {
              // Insert at specific position
              container.children[at].before(el);
            }
          }
        }
        break;

      case "delete":
        for (const item of items) {
          const el = document.getElementById(item.id);
          if (el) {
            el.remove();
          }
        }
        break;
    }

    boost.navEvent.sendNewContent();
  }

  /**
   * Parse HTML string to DOM element.
   * @param {string} html - HTML string
   * @returns {Element|null}
   * @private
   */
  _parseHtml(html) {
    return parser.parseFromString(html, "text/html").body.firstElementChild;
  }

  /**
   * Sends the current query string to the server.
   */
  sendQueryString() {
    // "?a=x&..." -> "a=x&..."
    let qs = document.location.search.slice(1);
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
    debugLog("send", `join ${name}`, { component_id });
    this._send("join", { name, state, children });
  }

  /**
   * Sends a leave request for a component.
   * @param {string} id - Component element ID
   */
  sendLeave(id) {
    debugLog("send", "leave", { id });
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
    debugLog("send", `user_event ${command}`, { id, explicit_args });
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
    const doSend = () => {
      if (this.isOpen) {
        this.socket.send(JSON.stringify(message));
      } else {
        this.messageQueue.push(message);
      }
    };

    // Apply latency simulation if enabled
    if (debugLatency > 0) {
      debugLog("latency", `Delaying ${debugLatency}ms`, { command });
      setTimeout(doSend, debugLatency);
    } else {
      doSend();
    }
  }
}

/** @type {ServerConnection} */
let connection = new ServerConnection();

/**
 * Represents a client-side wireview component.
 * Manages state synchronization with the server.
 *
 * Supports both legacy diff format (array) and Phoenix LiveView-style
 * diff format (object with static/dynamic parts).
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

    // Phoenix-style state (static/dynamic separation)
    /** @type {string[]|null} */
    this.static = null;
    /** @type {string[]} */
    this.dynamic = [];
    /** @type {string|null} */
    this.fingerprint = null;
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
   * Supports both legacy array format and Phoenix-style object format.
   * @param {Array<string|number>|Object} diff - Diff data
   */
  applyDiff(diff) {
    window.requestAnimationFrame(() => {
      let el = this.getElemenet();
      if (el) {
        // Remove loading classes before morphing
        this.clearLoadingClasses();

        let html;
        if (this.isPhoenixDiff(diff)) {
          html = this.applyPhoenixDiff(diff);
        } else {
          html = this.getHtml(diff);
        }

        if (html) {
          boost.morph(el, html);
          boost.navEvent.sendNewContent();
        }
      }
    });
  }

  /**
   * Check if diff is Phoenix-style format (object with 's' or numeric keys).
   * @param {*} diff - The diff to check
   * @returns {boolean}
   */
  isPhoenixDiff(diff) {
    return (
      diff !== null &&
      typeof diff === "object" &&
      !Array.isArray(diff)
    );
  }

  /**
   * Apply Phoenix-style diff (static/dynamic separation).
   * @param {PhoenixFullDiff|PhoenixPartialDiff} diff - Phoenix diff object
   * @returns {string} Reconstructed HTML
   */
  applyPhoenixDiff(diff) {
    if ("s" in diff) {
      // Full render: store static parts and dynamic values
      this.static = diff.s;
      this.dynamic = diff.d.slice(); // Clone to avoid mutation
      this.fingerprint = diff.f;
    } else {
      // Partial update: update only changed dynamic values
      for (const [idx, value] of Object.entries(diff)) {
        const index = parseInt(idx, 10);
        if (!isNaN(index) && index >= 0 && index < this.dynamic.length) {
          this.dynamic[index] = value;
        }
      }
    }

    return this.buildHtmlFromStatic();
  }

  /**
   * Build full HTML by interleaving static and dynamic parts.
   * @returns {string} Complete HTML string
   */
  buildHtmlFromStatic() {
    if (!this.static) {
      // Fallback: return current element's HTML
      return this.getElemenet()?.outerHTML || "";
    }

    const parts = [];
    for (let i = 0; i < this.static.length; i++) {
      parts.push(this.static[i]);
      if (i < this.dynamic.length) {
        parts.push(this.dynamic[i] || "");
      }
    }
    return parts.join("");
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

// Debug state
/** @type {boolean} */
var debugEnabled = false;
/** @type {number} */
var debugLatency = 0;

/**
 * Log a debug message if debug mode is enabled.
 * @param {string} category - Message category
 * @param {string} message - Log message
 * @param {*} [data] - Optional data to log
 */
function debugLog(category, message, data) {
  if (!debugEnabled) return;
  const timestamp = new Date().toISOString().substr(11, 12);
  const prefix = `%c[wireview ${timestamp}]%c ${category}:`;
  if (data !== undefined) {
    console.log(prefix, "color: #7c3aed; font-weight: bold", "color: #059669", message, data);
  } else {
    console.log(prefix, "color: #7c3aed; font-weight: bold", "color: #059669", message);
  }
}

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

  /**
   * Debug utilities for development and troubleshooting.
   */
  debug: {
    /**
     * Enable debug logging.
     * Logs WebSocket messages, component updates, and state changes.
     */
    enable() {
      debugEnabled = true;
      console.log(
        "%c[wireview]%c Debug mode enabled. Use wireview.debug.disable() to turn off.",
        "color: #7c3aed; font-weight: bold",
        "color: inherit"
      );
      this.status();
    },

    /**
     * Disable debug logging.
     */
    disable() {
      debugEnabled = false;
      console.log(
        "%c[wireview]%c Debug mode disabled.",
        "color: #7c3aed; font-weight: bold",
        "color: inherit"
      );
    },

    /**
     * Set artificial latency for testing slow connections.
     * @param {number} ms - Latency in milliseconds (0 to disable)
     */
    latency(ms) {
      debugLatency = Math.max(0, ms);
      if (debugLatency > 0) {
        console.log(
          `%c[wireview]%c Latency simulation: ${debugLatency}ms`,
          "color: #7c3aed; font-weight: bold",
          "color: #dc2626"
        );
      } else {
        console.log(
          "%c[wireview]%c Latency simulation disabled.",
          "color: #7c3aed; font-weight: bold",
          "color: inherit"
        );
      }
    },

    /**
     * Display current connection status and registered components.
     */
    status() {
      const componentIds = Object.keys(connection.components);
      const socketState = connection.socket
        ? ["CONNECTING", "OPEN", "CLOSING", "CLOSED"][connection.socket.readyState]
        : "NOT_INITIALIZED";

      console.group("%c[wireview] Status", "color: #7c3aed; font-weight: bold");
      console.log("WebSocket:", socketState);
      console.log("Debug:", debugEnabled ? "enabled" : "disabled");
      console.log("Latency simulation:", debugLatency > 0 ? `${debugLatency}ms` : "disabled");
      console.log("Components:", componentIds.length);
      if (componentIds.length > 0) {
        console.table(
          componentIds.map((id) => {
            const el = document.getElementById(id);
            return {
              id,
              name: el?.dataset?.name || "unknown",
            };
          })
        );
      }
      console.groupEnd();
    },

    /**
     * Get all registered components.
     * @returns {Object<string, WireviewComponent>}
     */
    components() {
      return connection.components;
    },

    /**
     * Get a specific component by ID.
     * @param {string} id - Component ID
     * @returns {WireviewComponent|undefined}
     */
    component(id) {
      return connection.components[id];
    },
  },
};
