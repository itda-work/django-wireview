import ReconnectingWebSocket from "reconnecting-websocket";
import { applyPartial, buildHtml } from "./rendered.mjs";
import boost from "./wireview-boost";

/**
 * @fileoverview Wireview client-side library for Django LiveView functionality.
 * Provides WebSocket-based real-time component updates.
 */

// Connection

const parser = new DOMParser();

/**
 * Parses a URL search string into a params object.
 * @param {string} search - Query string (with or without leading "?")
 * @returns {Object<string, string>}
 */
function parseQueryString(search) {
  const params = {};
  const searchParams = new URLSearchParams(search);
  for (const [key, value] of searchParams) {
    params[key] = value;
  }
  return params;
}

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
 * @property {Array<string|Object>} d - Dynamic parts (strings or comprehensions {s, d})
 * @property {string} f - Fingerprint
 */

/**
 * @typedef {Object<string, string|Object>} PhoenixPartialDiff
 * Partial diff with numeric string keys mapping to new values: a string, a
 * comprehension {s, d}, or a comprehension item update {u, n}
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
    /** @type {boolean} Track if we've been connected before (for reconnection detection) */
    this.wasConnected = false;
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

      // Notify hooks of reconnection (not on initial connect)
      if (this.wasConnected) {
        for (const component of Object.values(this.components)) {
          component.hookManager.reconnected();
        }
        // Restore form state on reconnection
        this.restoreFormState();
      }
      this.wasConnected = true;

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

      // Save form state before clearing components
      this.saveFormState();

      // Notify hooks of disconnection before clearing components
      for (const component of Object.values(this.components)) {
        component.hookManager.disconnected();
      }

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
        // End timing for profiling (event round-trip complete)
        endEventTiming();

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
            // Send params_changed after URL update
            {
              const urlObj = new URL(url, document.location.origin);
              const params = parseQueryString(urlObj.search);
              this.sendParamsChanged(url, params);
            }
            break;
          case "push":
            boost.HistoryCache.push(url);
            // Send params_changed after URL update
            {
              const urlObj = new URL(url, document.location.origin);
              const params = parseQueryString(urlObj.search);
              this.sendParamsChanged(url, params);
            }
            break;
        }
        break;

      case "title":
        var { title } = payload;
        document.title = title;
        break;

      case "flash":
        var { flash_type, message, timeout, dismissible } = payload;
        this.showFlash(flash_type, message, timeout, dismissible);
        break;

      case "clear_flash":
        var { flash_id } = payload;
        this.clearFlash(flash_id);
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
        var { op, stream, items, at, limit } = payload;
        this._handleStreamOp(op, stream, items, at, limit);
        break;

      case "upload_op":
        this._handleUploadOp(payload);
        break;

      case "exec_js":
        var { id, commands } = payload;
        var componentEl = document.getElementById(id);
        if (componentEl && commands) {
          wireview.exec(componentEl, commands);
        }
        break;

      case "hook_reply":
        // Response to a hook's pushEvent call
        var { ref, response } = payload;
        // Find the component that owns this callback
        for (const component of Object.values(this.components)) {
          if (component.hookManager.callbacks.has(ref)) {
            component.hookManager.handleReply(ref, response);
            break;
          }
        }
        break;

      case "push_event":
        // Server pushing event to hooks
        var { component_id, hook_id, event, payload: eventPayload } = payload;
        var targetComponent = this.components[component_id];
        if (targetComponent) {
          targetComponent.hookManager.handlePushEvent(hook_id, event, eventPayload);
        }
        break;

      default:
        console.warn(`[wireview] Unknown command "${command}"`, payload);
    }
  }

  /**
   * Handle upload operations from server.
   * @param {Object} payload - Upload operation payload
   * @private
   */
  _handleUploadOp(payload) {
    const { op, upload, ref, ...data } = payload;

    // Find the component that owns this upload
    for (const [componentId, component] of Object.entries(this.components)) {
      const manager = uploadManagers[componentId];
      if (manager && manager.configs[upload]) {
        switch (op) {
          case "config":
            manager.configure(upload, data);
            break;
          case "registered":
            manager.onRegistered(upload, ref, data.token, data.chunk_size, data.external);
            break;
          case "progress":
            manager.onProgress(upload, ref, data.progress, data.bytes_received);
            break;
          case "complete":
            manager.onComplete(upload, ref);
            break;
          case "error":
            manager.onError(upload, ref, data.errors || []);
            break;
          case "cancel":
            manager.onCancel(upload, ref);
            break;
        }
        return;
      }
    }

    // If no manager found, maybe it's a config for a component that just joined
    // Store it for later
    debugLog("upload", `No manager for upload op: ${op} ${upload}`);
  }

  /**
   * Handle stream operations (reset, insert, delete).
   * @param {string} op - Operation type ("reset", "insert", "delete")
   * @param {string} stream - Stream name (matches wire-stream attribute)
   * @param {Array<{id: string, html: string}>} items - Stream items
   * @param {number} at - Insert position (-1 = append, 0 = prepend)
   * @param {number} limit - Maximum items to keep (0 = no limit)
   * @private
   */
  _handleStreamOp(op, stream, items, at, limit = 0) {
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

    // Enforce limit by removing excess items
    if (limit > 0 && container.children.length > limit) {
      const excess = container.children.length - limit;
      // Remove from opposite end: if prepending (at=0), remove from end
      // If appending (at=-1), remove from start
      const removeFromEnd = at === 0;
      for (let i = 0; i < excess; i++) {
        if (removeFromEnd) {
          container.lastElementChild?.remove();
        } else {
          container.firstElementChild?.remove();
        }
      }
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
   * Sends URL parameter change notification to server.
   * @param {string} [uri] - Full URI (defaults to current location)
   * @param {Object<string, string>} [params] - Parsed params (defaults to current)
   */
  sendParamsChanged(uri, params) {
    uri = uri || document.location.href;
    params = params || parseQueryString(document.location.search);
    debugLog("send", "params_changed", { uri, params });
    this._send("params_changed", { uri, params });
  }

  /**
   * Sends the current query string to the server.
   * @deprecated Use sendParamsChanged() instead
   */
  sendQueryString() {
    this.sendParamsChanged();
  }

  /**
   * Shows a flash message to the user.
   * @param {string} flashType - Message type (success, error, info, warning)
   * @param {string} message - Message text
   * @param {number} timeout - Auto-dismiss timeout in ms (0 = no auto-dismiss)
   * @param {boolean} dismissible - Whether the message can be dismissed
   */
  showFlash(flashType, message, timeout, dismissible) {
    const container = document.querySelector("[wire-flash]");
    if (!container) {
      console.warn("wireview: No flash container found. Add an element with wire-flash attribute.");
      return;
    }

    const id = `flash-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    const el = document.createElement("div");
    el.id = id;
    el.className = `wireview-flash wireview-flash-${flashType}`;
    el.setAttribute("role", "alert");
    el.setAttribute("data-flash-type", flashType);

    // Create message content
    const messageSpan = document.createElement("span");
    messageSpan.className = "wireview-flash-message";
    messageSpan.textContent = message;
    el.appendChild(messageSpan);

    // Add dismiss button if dismissible
    if (dismissible) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "wireview-flash-dismiss";
      btn.setAttribute("aria-label", "Dismiss");
      btn.textContent = "×";
      btn.onclick = () => el.remove();
      el.appendChild(btn);
    }

    container.appendChild(el);

    // Trigger enter animation
    requestAnimationFrame(() => {
      el.classList.add("wireview-flash-enter");
    });

    // Auto-dismiss after timeout
    if (timeout > 0) {
      setTimeout(() => {
        el.classList.add("wireview-flash-exit");
        el.addEventListener("animationend", () => el.remove(), { once: true });
        // Fallback removal if animation doesn't trigger
        setTimeout(() => el.remove(), 500);
      }, timeout);
    }
  }

  /**
   * Clears flash message(s).
   * @param {string|null} flashId - Specific flash ID to clear, or null for all
   */
  clearFlash(flashId) {
    if (flashId) {
      const el = document.getElementById(flashId);
      if (el) {
        el.classList.add("wireview-flash-exit");
        el.addEventListener("animationend", () => el.remove(), { once: true });
        setTimeout(() => el.remove(), 500);
      }
    } else {
      document.querySelectorAll(".wireview-flash").forEach((el) => {
        el.classList.add("wireview-flash-exit");
        el.addEventListener("animationend", () => el.remove(), { once: true });
        setTimeout(() => el.remove(), 500);
      });
    }
  }

  /**
   * Saves form state for forms with wire-auto-recover attribute.
   * Called when WebSocket connection is lost.
   */
  saveFormState() {
    const forms = document.querySelectorAll("form[wire-auto-recover]");
    if (forms.length === 0) return;

    debugLog("form", `Saving state for ${forms.length} form(s)`);

    forms.forEach((form) => {
      const componentEl = form.closest("[wireview-component]");
      if (!componentEl) return;

      const key = `wireview-form-${componentEl.id}-${form.id || "default"}`;
      const formData = new FormData(/** @type {HTMLFormElement} */ (form));

      // Convert to serializable object, handling multiple values
      const data = {};
      for (const [name, value] of formData.entries()) {
        if (data[name]) {
          // Handle multiple values (checkboxes, multi-select)
          if (Array.isArray(data[name])) {
            data[name].push(value);
          } else {
            data[name] = [data[name], value];
          }
        } else {
          data[name] = value;
        }
      }

      // Skip if form is empty
      if (Object.keys(data).length === 0) return;

      try {
        sessionStorage.setItem(key, JSON.stringify(data));
        debugLog("form", `Saved form state: ${key}`, data);
      } catch (e) {
        console.warn("wireview: Failed to save form state", e);
      }
    });
  }

  /**
   * Restores form state for forms with wire-auto-recover attribute.
   * Called when WebSocket connection is re-established.
   */
  restoreFormState() {
    const forms = document.querySelectorAll("form[wire-auto-recover]");
    if (forms.length === 0) return;

    debugLog("form", `Restoring state for ${forms.length} form(s)`);

    forms.forEach((form) => {
      const componentEl = form.closest("[wireview-component]");
      if (!componentEl) return;

      const key = `wireview-form-${componentEl.id}-${form.id || "default"}`;
      const saved = sessionStorage.getItem(key);

      if (!saved) return;

      try {
        const data = JSON.parse(saved);
        debugLog("form", `Restoring form state: ${key}`, data);

        // Restore values to form fields
        Object.entries(data).forEach(([name, value]) => {
          const inputs = form.querySelectorAll(`[name="${name}"]`);

          inputs.forEach((input) => {
            const inputEl = /** @type {HTMLInputElement} */ (input);
            const inputType = inputEl.type?.toLowerCase();

            if (inputType === "checkbox" || inputType === "radio") {
              // Handle checkbox/radio
              const values = Array.isArray(value) ? value : [value];
              inputEl.checked = values.includes(inputEl.value);
            } else if (inputEl.tagName === "SELECT" && inputEl.multiple) {
              // Handle multi-select
              const values = Array.isArray(value) ? value : [value];
              Array.from(inputEl.options).forEach((opt) => {
                opt.selected = values.includes(opt.value);
              });
            } else {
              // Handle text, textarea, select, etc.
              inputEl.value = Array.isArray(value) ? value[0] : value;
            }
          });
        });

        // Check for custom recovery handler
        const handler = form.getAttribute("wire-auto-recover");
        if (handler && handler !== "" && handler !== "true") {
          // Send recovery event to server
          const componentId = componentEl.id;
          debugLog("form", `Calling recovery handler: ${handler}`);
          this._send("user_event", {
            id: componentId,
            command: handler,
            implicit_args: {},
            explicit_args: { form_data: data },
          });
        }

        // Clear saved state after restore
        sessionStorage.removeItem(key);
      } catch (e) {
        console.warn("wireview: Failed to restore form state", e);
        sessionStorage.removeItem(key);
      }
    });
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

    // Hook manager for JavaScript hooks
    /** @type {HookManager} */
    this.hookManager = new HookManager(this);

    // Viewport observer for infinite scroll
    /** @type {ViewportObserver} */
    this.viewportObserver = new ViewportObserver(this);
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
          // Call beforeUpdate on all hooks
          this.hookManager.beforeUpdate();

          // Profile patch time
          const patchStart = profilingEnabled ? performance.now() : 0;
          boost.morph(el, html);
          if (profilingEnabled) {
            recordPatchTime(performance.now() - patchStart);
          }
          boost.navEvent.sendNewContent();

          // Call updated on all hooks (and scan for new ones)
          this.hookManager.updated();

          // Update viewport observer (scan for new viewport elements)
          this.viewportObserver.updated();

          // Update upload previews (populate src for new preview elements)
          const uploadManager = uploadManagers[this.id];
          if (uploadManager) {
            uploadManager.updatePreviews();
          }

          // Update form feedback (manage wire-no-feedback classes)
          FeedbackManager.updated();
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
      // Partial update: strings, comprehensions, or comprehension item updates
      applyPartial(this.dynamic, diff);
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

    return buildHtml(this.static, this.dynamic);
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

      // Restore wire-disabled-with elements
      if (loadingEl._wireOriginalText !== undefined) {
        loadingEl.textContent = loadingEl._wireOriginalText;
        loadingEl.disabled = loadingEl._wireOriginalDisabled || false;
        delete loadingEl._wireOriginalText;
        delete loadingEl._wireOriginalDisabled;
      }
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

        // Initialize hooks after joining
        this.hookManager.init();

        // Initialize viewport observer for infinite scroll
        this.viewportObserver.init();
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

// ============================================================================
// JavaScript Hooks
// ============================================================================

/**
 * @typedef {Object} HookDefinition
 * @property {function(): void} [mounted] - Called after element joins
 * @property {function(): void} [beforeUpdate] - Called before morph (sync)
 * @property {function(): void} [updated] - Called after morph
 * @property {function(): void} [destroyed] - Called when element removed
 * @property {function(): void} [disconnected] - Called on WebSocket close
 * @property {function(): void} [reconnected] - Called on WebSocket reopen
 */

/**
 * Context object for hook callbacks.
 * Provides access to DOM element and server communication.
 */
class HookContext {
  /**
   * @param {HTMLElement} el - The hook element
   * @param {string} hookId - Unique identifier for this hook instance
   * @param {string} hookName - Name of the hook definition
   * @param {HookManager} manager - Parent manager reference
   */
  constructor(el, hookId, hookName, manager) {
    /** @type {HTMLElement} The DOM element this hook is attached to */
    this.el = el;
    /** @private */
    this.__hookId = hookId;
    /** @private */
    this.__hookName = hookName;
    /** @private */
    this.__manager = manager;
    /** @private @type {Map<string, Function[]>} */
    this.__eventHandlers = new Map();
  }

  /**
   * Push an event to the server.
   * @param {string} event - Event name
   * @param {Object} [payload] - Event data
   * @param {function(any): void} [callback] - Response callback
   */
  pushEvent(event, payload = {}, callback = null) {
    this.__manager.pushEvent(this.__hookId, event, payload, callback);
  }

  /**
   * Register a handler for server-sent events.
   * @param {string} event - Event name to listen for
   * @param {function(Object): void} callback - Handler function
   */
  handleEvent(event, callback) {
    if (!this.__eventHandlers.has(event)) {
      this.__eventHandlers.set(event, []);
    }
    this.__eventHandlers.get(event).push(callback);
  }

  /**
   * Internal: dispatch event from server.
   * @param {string} event - Event name
   * @param {Object} payload - Event data
   * @private
   */
  __dispatchEvent(event, payload) {
    const handlers = this.__eventHandlers.get(event) || [];
    handlers.forEach((cb) => {
      try {
        cb(payload);
      } catch (e) {
        console.error(`[wireview] Error in ${this.__hookName} handleEvent("${event}"):`, e);
      }
    });
  }

  /**
   * Internal: cleanup handlers.
   * @private
   */
  __destroy() {
    this.__eventHandlers.clear();
  }
}

/**
 * Manages all hook instances for a component.
 */
class HookManager {
  /**
   * @param {WireviewComponent} component - Parent component
   */
  constructor(component) {
    /** @type {WireviewComponent} */
    this.component = component;
    /** @type {Map<string, HookContext>} hookId -> HookContext */
    this.instances = new Map();
    /** @type {Map<string, Function>} ref -> callback */
    this.callbacks = new Map();
    /** @type {number} */
    this.refCounter = 0;
    /** @type {MutationObserver|null} */
    this.observer = null;
  }

  /**
   * Initialize hooks after component joins.
   */
  init() {
    this.scanAndMount();
    this.setupMutationObserver();
  }

  /**
   * Scan DOM for wire-hook elements and mount hooks.
   */
  scanAndMount() {
    const root = this.component.getElemenet();
    if (!root) return;

    const hookElements = root.querySelectorAll("[wire-hook]");
    hookElements.forEach((el) => {
      const element = /** @type {HTMLElement} */ (el);
      // Also check the root element itself
      if (!element.__wireviewHookIds) {
        this.mountHooks(element);
      }
    });

    // Check if root itself has hooks
    if (root.hasAttribute("wire-hook") && !root.__wireviewHookIds) {
      this.mountHooks(root);
    }
  }

  /**
   * Mount hooks on an element (supports multiple hooks separated by space).
   * @param {HTMLElement} el
   */
  mountHooks(el) {
    const hookAttr = el.getAttribute("wire-hook");
    if (!hookAttr) return;

    const hookNames = hookAttr.split(/\s+/).filter(Boolean);
    /** @type {string[]} */
    el.__wireviewHookIds = [];

    for (const hookName of hookNames) {
      const definition = window.wireview.hooks[hookName];
      if (!definition) {
        console.warn(`[wireview] Hook "${hookName}" not registered`);
        continue;
      }

      const hookId = this.generateHookId(hookName);
      el.__wireviewHookIds.push(hookId);

      const context = new HookContext(el, hookId, hookName, this);

      // Copy definition methods to context
      for (const key of Object.keys(definition)) {
        if (typeof definition[key] === "function") {
          context[key] = definition[key].bind(context);
        }
      }

      this.instances.set(hookId, context);

      // Call mounted()
      if (context.mounted) {
        try {
          context.mounted();
        } catch (e) {
          console.error(`[wireview] Error in ${hookName}.mounted():`, e);
        }
      }

      debugLog("hook", `Mounted: ${hookName}`, { hookId, el: el.id || el.tagName });
    }
  }

  /**
   * Generate unique hook ID.
   * @param {string} hookName
   * @returns {string}
   */
  generateHookId(hookName) {
    const componentId = this.component.id;
    return `${componentId}:${hookName}:${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
  }

  /**
   * Setup MutationObserver to detect removed elements.
   */
  setupMutationObserver() {
    const root = this.component.getElemenet();
    if (!root) return;

    this.observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.removedNodes) {
          if (node.nodeType === Node.ELEMENT_NODE) {
            this.handleRemovedNode(/** @type {HTMLElement} */ (node));
          }
        }
      }
    });

    this.observer.observe(root, {
      childList: true,
      subtree: true,
    });
  }

  /**
   * Handle removed DOM node.
   * @param {HTMLElement} node
   */
  handleRemovedNode(node) {
    // Check if the node itself has hooks
    if (node.__wireviewHookIds) {
      for (const hookId of node.__wireviewHookIds) {
        this.destroyHook(hookId);
      }
    }
    // Check children too
    if (node.querySelectorAll) {
      const hookElements = node.querySelectorAll("[wire-hook]");
      for (const el of hookElements) {
        const element = /** @type {HTMLElement} */ (el);
        if (element.__wireviewHookIds) {
          for (const hookId of element.__wireviewHookIds) {
            this.destroyHook(hookId);
          }
        }
      }
    }
  }

  /**
   * Destroy a hook instance.
   * @param {string} hookId
   */
  destroyHook(hookId) {
    const context = this.instances.get(hookId);
    if (!context) return;

    if (context.destroyed) {
      try {
        context.destroyed();
      } catch (e) {
        console.error(`[wireview] Error in ${context.__hookName}.destroyed():`, e);
      }
    }

    debugLog("hook", `Destroyed: ${context.__hookName}`, { hookId });
    context.__destroy();
    this.instances.delete(hookId);
  }

  /**
   * Called before DOM morph.
   */
  beforeUpdate() {
    for (const context of this.instances.values()) {
      if (context.beforeUpdate) {
        try {
          context.beforeUpdate();
        } catch (e) {
          console.error(`[wireview] Error in ${context.__hookName}.beforeUpdate():`, e);
        }
      }
    }
  }

  /**
   * Called after DOM morph.
   */
  updated() {
    // First scan for new hooks that may have been added during morph
    this.scanAndMount();

    // Then call updated() on existing hooks
    for (const context of this.instances.values()) {
      if (context.updated) {
        try {
          context.updated();
        } catch (e) {
          console.error(`[wireview] Error in ${context.__hookName}.updated():`, e);
        }
      }
    }
  }

  /**
   * Called when WebSocket disconnects.
   */
  disconnected() {
    for (const context of this.instances.values()) {
      if (context.disconnected) {
        try {
          context.disconnected();
        } catch (e) {
          console.error(`[wireview] Error in ${context.__hookName}.disconnected():`, e);
        }
      }
    }
  }

  /**
   * Called when WebSocket reconnects.
   */
  reconnected() {
    for (const context of this.instances.values()) {
      if (context.reconnected) {
        try {
          context.reconnected();
        } catch (e) {
          console.error(`[wireview] Error in ${context.__hookName}.reconnected():`, e);
        }
      }
    }
  }

  /**
   * Send hook event to server.
   * @param {string} hookId
   * @param {string} event
   * @param {Object} payload
   * @param {Function|null} callback
   */
  pushEvent(hookId, event, payload, callback) {
    const ref = callback ? `hook-${++this.refCounter}` : null;

    if (ref && callback) {
      this.callbacks.set(ref, callback);
    }

    connection._send("hook_event", {
      component_id: this.component.id,
      hook_id: hookId,
      event: event,
      payload: payload,
      ref: ref,
    });

    debugLog("hook", `pushEvent: ${event}`, { hookId, payload, ref });
  }

  /**
   * Handle hook reply from server.
   * @param {string} ref
   * @param {any} response
   */
  handleReply(ref, response) {
    const callback = this.callbacks.get(ref);
    if (callback) {
      this.callbacks.delete(ref);
      try {
        callback(response);
      } catch (e) {
        console.error("[wireview] Error in pushEvent callback:", e);
      }
    }
  }

  /**
   * Handle push_event from server.
   * @param {string|null} hookId - Target hook ID (null = broadcast to all)
   * @param {string} event
   * @param {Object} payload
   */
  handlePushEvent(hookId, event, payload) {
    if (hookId) {
      // Target specific hook
      const context = this.instances.get(hookId);
      if (context) {
        context.__dispatchEvent(event, payload);
      }
    } else {
      // Broadcast to all hooks
      for (const context of this.instances.values()) {
        context.__dispatchEvent(event, payload);
      }
    }
  }

  /**
   * Cleanup all hooks.
   */
  destroy() {
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
    for (const hookId of this.instances.keys()) {
      this.destroyHook(hookId);
    }
    this.callbacks.clear();
  }
}

// ============================================================================
// Viewport Observer (Infinite Scroll)
// ============================================================================

/**
 * Manages viewport bindings for infinite scroll functionality.
 * Observes elements with wire-viewport-top and wire-viewport-bottom attributes.
 */
class ViewportObserver {
  /**
   * @param {WireviewComponent} component - Parent component
   */
  constructor(component) {
    /** @type {WireviewComponent} */
    this.component = component;
    /** @type {IntersectionObserver|null} */
    this.observer = null;
    /** @type {Map<Element, {type: string, handler: string}>} */
    this.observed = new Map();
    /** @type {number} */
    this.lastScrollY = 0;
    /** @type {boolean} */
    this.pendingTop = false;
    /** @type {boolean} */
    this.pendingBottom = false;
  }

  /**
   * Initialize the viewport observer.
   */
  init() {
    this.setupObserver();
    this.scanAndObserve();
    this.lastScrollY = window.scrollY;
  }

  /**
   * Setup the IntersectionObserver.
   */
  setupObserver() {
    if (this.observer) return;

    this.observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            this.handleIntersection(entry.target);
          }
        }
      },
      {
        root: null, // Use viewport
        rootMargin: "0px",
        threshold: 0,
      }
    );
  }

  /**
   * Scan for viewport binding elements and observe them.
   */
  scanAndObserve() {
    const root = this.component.getElemenet();
    if (!root) return;

    // Find all elements with viewport bindings
    const topElements = root.querySelectorAll("[wire-viewport-top]");
    const bottomElements = root.querySelectorAll("[wire-viewport-bottom]");

    // Observe top viewport elements
    topElements.forEach((el) => {
      const handler = el.getAttribute("wire-viewport-top");
      if (handler && !this.observed.has(el)) {
        this.observed.set(el, { type: "top", handler });
        this.observer?.observe(el);
        debugLog("viewport", `Observing top: ${handler}`, { el });
      }
    });

    // Observe bottom viewport elements
    bottomElements.forEach((el) => {
      const handler = el.getAttribute("wire-viewport-bottom");
      if (handler && !this.observed.has(el)) {
        this.observed.set(el, { type: "bottom", handler });
        this.observer?.observe(el);
        debugLog("viewport", `Observing bottom: ${handler}`, { el });
      }
    });
  }

  /**
   * Handle element intersection with viewport.
   * @param {Element} element
   */
  handleIntersection(element) {
    const info = this.observed.get(element);
    if (!info) return;

    const currentScrollY = window.scrollY;
    const scrollingDown = currentScrollY > this.lastScrollY;
    const scrollingUp = currentScrollY < this.lastScrollY;

    // Determine if overran (rapid scroll past boundary)
    const overran = this.detectOverran(info.type, element);

    // For top viewport, trigger when scrolling up or overran
    // For bottom viewport, trigger when scrolling down or overran
    const shouldTrigger =
      (info.type === "top" && (scrollingUp || overran)) ||
      (info.type === "bottom" && (scrollingDown || overran));

    if (shouldTrigger) {
      this.sendViewportEvent(info.handler, overran);
    }

    this.lastScrollY = currentScrollY;
  }

  /**
   * Detect if the viewport has overran the boundary.
   * @param {string} type - "top" or "bottom"
   * @param {Element} element
   * @returns {boolean}
   */
  detectOverran(type, element) {
    const rect = element.getBoundingClientRect();

    if (type === "top") {
      // Overran if the element is well below the top of viewport
      // (user scrolled rapidly to top)
      return rect.top > window.innerHeight * 0.5;
    } else {
      // Overran if the element is well above the bottom of viewport
      // (user scrolled rapidly to bottom)
      return rect.bottom < window.innerHeight * 0.5;
    }
  }

  /**
   * Send viewport event to server.
   * @param {string} handler - Event handler name
   * @param {boolean} overran - Whether the viewport was overran
   */
  sendViewportEvent(handler, overran) {
    const componentId = this.component.id;

    debugLog("viewport", `Sending event: ${handler}`, { overran });

    connection.sendUserEvent(componentId, handler, {}, { _overran: overran });
  }

  /**
   * Called after DOM morph to re-scan for viewport elements.
   */
  updated() {
    // Clean up removed elements
    for (const [el, info] of this.observed) {
      if (!document.contains(el)) {
        this.observer?.unobserve(el);
        this.observed.delete(el);
        debugLog("viewport", `Unobserved: ${info.handler}`, { el });
      }
    }

    // Scan for new elements
    this.scanAndObserve();
  }

  /**
   * Cleanup the observer.
   */
  destroy() {
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
    this.observed.clear();
  }
}

// ============================================================================
// Upload Manager
// ============================================================================

/**
 * @typedef {Object} UploadConfig
 * @property {string[]} accept - Accepted file extensions
 * @property {number} max_entries - Maximum concurrent uploads
 * @property {number} max_file_size - Max file size in bytes
 * @property {number} chunk_size - Chunk size for uploads
 * @property {boolean} auto_upload - Auto-start uploads
 * @property {string} endpoint - HTTP upload endpoint
 */

/**
 * @typedef {Object} UploadEntry
 * @property {string} ref - Unique reference
 * @property {File} file - The File object
 * @property {string} status - Upload status
 * @property {number} progress - 0-100
 * @property {string[]} errors - Error messages
 * @property {string|null} token - Upload token (from server)
 * @property {AbortController|null} controller - For cancellation
 */

/** @type {Object<string, UploadManager>} */
const uploadManagers = {};

/**
 * Manages file uploads for a component.
 */
class UploadManager {
  /**
   * @param {string} componentId
   */
  constructor(componentId) {
    this.componentId = componentId;
    /** @type {Object<string, UploadConfig>} */
    this.configs = {};
    /** @type {Object<string, Object<string, UploadEntry>>} */
    this.entries = {};
  }

  /**
   * Configure an upload field.
   * @param {string} name
   * @param {UploadConfig} config
   */
  configure(name, config) {
    this.configs[name] = config;
    this.entries[name] = this.entries[name] || {};
    debugLog("upload", `Configured upload: ${name}`, config);
  }

  /**
   * Add files to an upload field.
   * @param {string} name - Upload field name
   * @param {FileList|File[]} files - Files to upload
   */
  addFiles(name, files) {
    const config = this.configs[name];
    if (!config) {
      console.error(`[wireview] Unknown upload: ${name}`);
      return;
    }

    const newEntries = [];
    for (const file of Array.from(files)) {
      // Client-side validation
      const errors = this._validateFile(file, config);

      const ref = this._generateRef();
      /** @type {UploadEntry} */
      const entry = {
        ref: ref,
        file: file,
        status: errors.length ? "error" : "pending",
        progress: 0,
        errors: errors,
        token: null,
        controller: null,
      };

      this.entries[name][ref] = entry;
      newEntries.push({
        ref: ref,
        name: file.name,
        size: file.size,
        type: file.type,
      });
    }

    // Register with server
    if (newEntries.length > 0) {
      connection._send("upload_register", {
        id: this.componentId,
        name: name,
        entries: newEntries,
      });
    }

    // Update preview images
    this.updatePreviews(name);

    // Dispatch event for UI updates
    this._dispatchEvent("upload:added", { upload: name });
  }

  /**
   * Update preview images for an upload field.
   * Finds img elements with wire-preview attribute and sets their src.
   * @param {string} name - Upload field name (optional, updates all if not specified)
   */
  updatePreviews(name) {
    const entries = name ? this.entries[name] : null;

    // Find all preview elements in the document
    const selector = name
      ? `[wire-preview^="${name}:"]`
      : "[wire-preview]";
    const previewElements = document.querySelectorAll(selector);

    previewElements.forEach((img) => {
      const previewAttr = img.getAttribute("wire-preview");
      if (!previewAttr) return;

      const [uploadName, ref] = previewAttr.split(":");
      if (!uploadName || !ref) return;

      const entry = this.entries[uploadName]?.[ref];
      if (!entry || !entry.file) return;

      // Only set preview for image files
      if (entry.file.type.startsWith("image/")) {
        // Check if src is already set to avoid recreating blob URL
        if (!img.src || img.src === "" || img.src === window.location.href) {
          const url = URL.createObjectURL(entry.file);
          img.src = url;
          // Store URL for cleanup
          img._blobUrl = url;
          debugLog("upload", `Set preview: ${uploadName}/${ref}`);
        }
      }
    });
  }

  /**
   * Clean up blob URLs for preview images.
   * Called when entries are removed or component is destroyed.
   * @param {string} name - Upload field name
   * @param {string} ref - Entry reference (optional, cleans all for name if not specified)
   */
  cleanupPreviews(name, ref) {
    const selector = ref
      ? `[wire-preview="${name}:${ref}"]`
      : `[wire-preview^="${name}:"]`;
    const previewElements = document.querySelectorAll(selector);

    previewElements.forEach((img) => {
      if (img._blobUrl) {
        URL.revokeObjectURL(img._blobUrl);
        delete img._blobUrl;
      }
    });
  }

  /**
   * Handle registered response from server.
   * @param {string} name
   * @param {string} ref
   * @param {string} token - Upload token (for chunked uploads)
   * @param {number} chunkSize - Chunk size (for chunked uploads)
   * @param {Object} external - External upload metadata (for S3/GCS uploads)
   */
  onRegistered(name, ref, token, chunkSize, external) {
    const entry = this.entries[name]?.[ref];
    if (!entry) return;

    entry.token = token;
    entry.external = external;  // Store external upload metadata
    entry.status = "registered";
    debugLog("upload", `Registered: ${name}/${ref}`, external ? "(external)" : "(chunked)");

    const config = this.configs[name];
    if (config?.auto_upload) {
      if (external) {
        this.startExternalUpload(name, ref);
      } else {
        this.startUpload(name, ref);
      }
    }
  }

  /**
   * Handle progress update from server.
   * @param {string} name
   * @param {string} ref
   * @param {number} progress
   * @param {number} bytesReceived
   */
  onProgress(name, ref, progress, bytesReceived) {
    const entry = this.entries[name]?.[ref];
    if (entry) {
      entry.progress = progress;
      this._dispatchEvent("upload:progress", { upload: name, ref, progress });
    }
  }

  /**
   * Handle upload complete from server.
   * @param {string} name
   * @param {string} ref
   */
  onComplete(name, ref) {
    const entry = this.entries[name]?.[ref];
    if (entry) {
      entry.status = "completed";
      entry.progress = 100;
      this._dispatchEvent("upload:complete", { upload: name, ref });
    }
  }

  /**
   * Handle error from server.
   * @param {string} name
   * @param {string} ref
   * @param {string[]} errors
   */
  onError(name, ref, errors) {
    const entry = this.entries[name]?.[ref];
    if (entry) {
      entry.status = "error";
      entry.errors = errors;
      this._dispatchEvent("upload:error", { upload: name, ref, errors });
    }
  }

  /**
   * Handle cancel from server.
   * @param {string} name
   * @param {string} ref
   */
  onCancel(name, ref) {
    const entry = this.entries[name]?.[ref];
    if (entry) {
      entry.status = "cancelled";
      entry.controller?.abort();
      // Clean up preview blob URL
      this.cleanupPreviews(name, ref);
      this._dispatchEvent("upload:cancel", { upload: name, ref });
    }
  }

  /**
   * Start uploading an entry.
   * @param {string} name
   * @param {string} ref
   */
  async startUpload(name, ref) {
    const config = this.configs[name];
    const entry = this.entries[name]?.[ref];
    if (!entry || !entry.token || !config) return;

    entry.status = "uploading";
    entry.controller = new AbortController();

    const chunkSize = config.chunk_size;
    const file = entry.file;
    const totalChunks = Math.ceil(file.size / chunkSize);

    debugLog("upload", `Starting upload: ${name}/${ref} (${totalChunks} chunks)`);

    try {
      for (let i = 0; i < totalChunks; i++) {
        if (entry.status === "cancelled") break;

        const start = i * chunkSize;
        const end = Math.min(start + chunkSize, file.size);
        const chunk = file.slice(start, end);

        const response = await fetch(config.endpoint, {
          method: "POST",
          headers: {
            "X-Upload-Token": entry.token,
            "X-Chunk-Index": String(i),
            "X-Total-Chunks": String(totalChunks),
            "X-Entry-Ref": ref,
            "Content-Type": "application/octet-stream",
          },
          body: chunk,
          signal: entry.controller.signal,
        });

        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.error || "Upload failed");
        }

        const result = await response.json();
        entry.progress = result.progress;

        // Update progress in UI
        this._dispatchEvent("upload:progress", {
          upload: name,
          ref,
          progress: result.progress,
        });

        if (result.complete) {
          // Notify server that upload is complete
          connection._send("upload_complete", {
            id: this.componentId,
            name: name,
            ref: ref,
          });
          entry.status = "completed";
          entry.progress = 100;
        }
      }
    } catch (error) {
      if (error.name === "AbortError") {
        entry.status = "cancelled";
        debugLog("upload", `Cancelled: ${name}/${ref}`);
      } else {
        entry.status = "error";
        entry.errors.push(error.message);
        debugLog("upload", `Error: ${name}/${ref}`, error.message);
      }
    }
  }

  /**
   * Start an external upload (S3, GCS, etc.).
   * Uploads directly to external storage using presigned URL.
   * @param {string} name
   * @param {string} ref
   */
  async startExternalUpload(name, ref) {
    const entry = this.entries[name]?.[ref];
    if (!entry || !entry.external) return;

    entry.status = "uploading";
    entry.controller = new AbortController();

    const { url, method = "PUT", headers = {} } = entry.external;
    const file = entry.file;

    debugLog("upload", `Starting external upload: ${name}/${ref} to ${url}`);

    try {
      const xhr = new XMLHttpRequest();

      // Track progress
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          const progress = Math.round((event.loaded / event.total) * 100);
          entry.progress = progress;
          this._dispatchEvent("upload:progress", { upload: name, ref, progress });
        }
      });

      // Handle completion
      const uploadPromise = new Promise((resolve, reject) => {
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve();
          } else {
            reject(new Error(`Upload failed: ${xhr.status} ${xhr.statusText}`));
          }
        };
        xhr.onerror = () => reject(new Error("Network error during upload"));
        xhr.onabort = () => reject(new DOMException("Upload aborted", "AbortError"));
      });

      // Setup abort handling
      entry.controller.signal.addEventListener("abort", () => xhr.abort());

      // Start upload
      xhr.open(method, url, true);

      // Add custom headers
      for (const [key, value] of Object.entries(headers)) {
        xhr.setRequestHeader(key, value);
      }

      // Set Content-Type if not specified
      if (!headers["Content-Type"] && file.type) {
        xhr.setRequestHeader("Content-Type", file.type);
      }

      xhr.send(file);
      await uploadPromise;

      // Notify server that external upload is complete
      connection._send("upload_complete", {
        id: this.componentId,
        name: name,
        ref: ref,
      });

      entry.status = "completed";
      entry.progress = 100;
      this._dispatchEvent("upload:complete", { upload: name, ref });
      debugLog("upload", `External upload complete: ${name}/${ref}`);

    } catch (error) {
      if (error.name === "AbortError") {
        entry.status = "cancelled";
        debugLog("upload", `External upload cancelled: ${name}/${ref}`);
      } else {
        entry.status = "error";
        entry.errors.push(error.message);
        debugLog("upload", `External upload error: ${name}/${ref}`, error.message);
        this._dispatchEvent("upload:error", { upload: name, ref, errors: [error.message] });
      }
    }
  }

  /**
   * Cancel an upload.
   * @param {string} name
   * @param {string} ref
   */
  cancel(name, ref) {
    const entry = this.entries[name]?.[ref];
    if (entry) {
      entry.controller?.abort();
      entry.status = "cancelled";
      connection._send("upload_cancel", {
        id: this.componentId,
        name: name,
        ref: ref,
      });
    }
  }

  /**
   * Get preview URL for an image file.
   * @param {string} name
   * @param {string} ref
   * @returns {string|null}
   */
  getPreviewUrl(name, ref) {
    const entry = this.entries[name]?.[ref];
    if (entry && entry.file.type.startsWith("image/")) {
      return URL.createObjectURL(entry.file);
    }
    return null;
  }

  /**
   * Get all entries for an upload.
   * @param {string} name
   * @returns {UploadEntry[]}
   */
  getEntries(name) {
    return Object.values(this.entries[name] || {});
  }

  /**
   * Validate a file against config.
   * @param {File} file
   * @param {UploadConfig} config
   * @returns {string[]} Error messages
   * @private
   */
  _validateFile(file, config) {
    const errors = [];

    // Check extension
    if (config.accept && config.accept.length > 0) {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      const accepted = config.accept.map((a) => a.toLowerCase());
      if (!accepted.includes(ext)) {
        errors.push(`Invalid file type: ${ext}`);
      }
    }

    // Check size
    if (file.size > config.max_file_size) {
      errors.push(`File too large: ${this._formatSize(file.size)}`);
    }

    return errors;
  }

  /**
   * Generate a unique upload reference.
   * @returns {string}
   * @private
   */
  _generateRef() {
    return `upload-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
  }

  /**
   * Format file size for display.
   * @param {number} bytes
   * @returns {string}
   * @private
   */
  _formatSize(bytes) {
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
      bytes /= 1024;
      i++;
    }
    return `${bytes.toFixed(1)} ${units[i]}`;
  }

  /**
   * Dispatch a custom event on the component element.
   * @param {string} eventName
   * @param {Object} detail
   * @private
   */
  _dispatchEvent(eventName, detail) {
    const el = document.getElementById(this.componentId);
    if (el) {
      el.dispatchEvent(
        new CustomEvent(eventName, {
          detail: { ...detail, componentId: this.componentId },
          bubbles: true,
        })
      );
    }
  }
}

/**
 * Get or create upload manager for a component.
 * @param {string} componentId
 * @returns {UploadManager}
 */
function getUploadManager(componentId) {
  if (!uploadManagers[componentId]) {
    uploadManagers[componentId] = new UploadManager(componentId);
  }
  return uploadManagers[componentId];
}

// Initialize drag-and-drop support
function initUploadDropZones() {
  document.addEventListener("dragover", (e) => {
    const dropZone = /** @type {HTMLElement|null} */ (
      e.target instanceof Element ? e.target.closest("[wire-upload-drop]") : null
    );
    if (dropZone) {
      e.preventDefault();
      dropZone.classList.add("wireview-drag-over");
    }
  });

  document.addEventListener("dragleave", (e) => {
    const dropZone = /** @type {HTMLElement|null} */ (
      e.target instanceof Element ? e.target.closest("[wire-upload-drop]") : null
    );
    if (dropZone && !dropZone.contains(/** @type {Node|null} */ (e.relatedTarget))) {
      dropZone.classList.remove("wireview-drag-over");
    }
  });

  document.addEventListener("drop", (e) => {
    const dropZone = /** @type {HTMLElement|null} */ (
      e.target instanceof Element ? e.target.closest("[wire-upload-drop]") : null
    );
    if (dropZone) {
      e.preventDefault();
      dropZone.classList.remove("wireview-drag-over");

      const uploadName = dropZone.getAttribute("wire-upload-drop");
      const componentEl = dropZone.closest("[wireview-component]");

      if (uploadName && componentEl && e.dataTransfer?.files.length) {
        const manager = getUploadManager(componentEl.id);
        manager.addFiles(uploadName, e.dataTransfer.files);
      }
    }
  });
}

// Initialize on load
initUploadDropZones();

// ============================================================================
// Form Feedback System
// ============================================================================

/**
 * Manages form feedback visibility based on field interaction.
 * Elements with wire-feedback-for="field_name" initially have wire-no-feedback
 * class which hides them. When the corresponding field is touched (blurred or
 * changed), the class is removed to show the feedback.
 */
const FeedbackManager = {
  /** @type {Set<string>} */
  touchedFields: new Set(),

  /**
   * Initialize the feedback system.
   * Sets up event delegation for tracking field touches.
   */
  init() {
    // Track blur events on form inputs (field was touched)
    document.addEventListener("blur", (e) => {
      const target = /** @type {HTMLElement} */ (e.target);
      if (this.isFormInput(target)) {
        const name = this.getFieldName(target);
        if (name) {
          this.markTouched(name);
        }
      }
    }, true);  // Use capture to ensure we get the event

    // Track change events (for select, checkbox, radio)
    document.addEventListener("change", (e) => {
      const target = /** @type {HTMLElement} */ (e.target);
      if (this.isFormInput(target)) {
        const name = this.getFieldName(target);
        if (name) {
          this.markTouched(name);
        }
      }
    }, true);

    // Track input events (for immediate feedback on typing)
    document.addEventListener("input", (e) => {
      const target = /** @type {HTMLElement} */ (e.target);
      if (this.isFormInput(target)) {
        const name = this.getFieldName(target);
        if (name && this.touchedFields.has(name)) {
          // Already touched - ensure feedback is shown
          this.showFeedback(name);
        }
      }
    }, true);

    debugLog("feedback", "Feedback system initialized");
  },

  /**
   * Check if element is a form input.
   * @param {HTMLElement} el
   * @returns {boolean}
   */
  isFormInput(el) {
    return el.tagName === "INPUT" ||
           el.tagName === "TEXTAREA" ||
           el.tagName === "SELECT";
  },

  /**
   * Get the field name from an input element.
   * @param {HTMLElement} el
   * @returns {string|null}
   */
  getFieldName(el) {
    return el.getAttribute("name") || el.getAttribute("id") || null;
  },

  /**
   * Mark a field as touched and show its feedback.
   * @param {string} name
   */
  markTouched(name) {
    if (this.touchedFields.has(name)) return;

    this.touchedFields.add(name);
    this.showFeedback(name);
    debugLog("feedback", `Field touched: ${name}`);
  },

  /**
   * Show feedback for a field by removing wire-no-feedback class.
   * @param {string} name
   */
  showFeedback(name) {
    // Find all feedback elements for this field
    const feedbackEls = document.querySelectorAll(`[wire-feedback-for="${name}"]`);
    feedbackEls.forEach((el) => {
      el.classList.remove("wire-no-feedback");
    });
  },

  /**
   * Reset touched state (called on form reset or navigation).
   */
  reset() {
    this.touchedFields.clear();
    // Re-add wire-no-feedback to all feedback elements
    const feedbackEls = document.querySelectorAll("[wire-feedback-for]");
    feedbackEls.forEach((el) => {
      el.classList.add("wire-no-feedback");
    });
    debugLog("feedback", "Feedback state reset");
  },

  /**
   * Called after DOM morph to handle new feedback elements.
   * New elements should have wire-no-feedback unless their field is touched.
   */
  updated() {
    const feedbackEls = document.querySelectorAll("[wire-feedback-for]");
    feedbackEls.forEach((el) => {
      const fieldName = el.getAttribute("wire-feedback-for");
      if (fieldName && this.touchedFields.has(fieldName)) {
        // Field is touched - show feedback
        el.classList.remove("wire-no-feedback");
      } else {
        // Field not touched - ensure feedback is hidden
        el.classList.add("wire-no-feedback");
      }
    });
  }
};

// Initialize feedback system
FeedbackManager.init();

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
/** @type {boolean} */
var profilingEnabled = false;

/**
 * Performance metrics storage for profiling.
 * @type {{
 *   patchTimes: number[],
 *   roundTripTimes: number[],
 *   eventCount: number,
 *   lastEventStart: number,
 *   startTime: number
 * }}
 */
var profilingMetrics = {
  patchTimes: [],
  roundTripTimes: [],
  eventCount: 0,
  lastEventStart: 0,
  startTime: 0,
};

/**
 * Record a patch/morph operation time.
 * @param {number} duration - Duration in milliseconds
 */
function recordPatchTime(duration) {
  if (!profilingEnabled) return;
  profilingMetrics.patchTimes.push(duration);
  console.log(
    `%c[wireview profiler]%c Patch: ${duration.toFixed(2)}ms`,
    "color: #059669; font-weight: bold",
    "color: inherit"
  );
}

/**
 * Record a round-trip time (event send to response receive).
 * @param {number} duration - Duration in milliseconds
 */
function recordRoundTrip(duration) {
  if (!profilingEnabled) return;
  profilingMetrics.roundTripTimes.push(duration);
  profilingMetrics.eventCount++;
  console.log(
    `%c[wireview profiler]%c Round-trip: ${duration.toFixed(2)}ms`,
    "color: #059669; font-weight: bold",
    "color: inherit"
  );
}

/**
 * Start timing an event.
 */
function startEventTiming() {
  if (!profilingEnabled) return;
  profilingMetrics.lastEventStart = performance.now();
}

/**
 * End timing an event and record round-trip.
 */
function endEventTiming() {
  if (!profilingEnabled || profilingMetrics.lastEventStart === 0) return;
  const duration = performance.now() - profilingMetrics.lastEventStart;
  recordRoundTrip(duration);
  profilingMetrics.lastEventStart = 0;
}

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

    case "set_value":
      if (target && "value" in target) {
        /** @type {HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement} */ (
          target
        ).value = cmd.value ?? "";
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
   * User-defined hook definitions.
   * Register hooks by adding them to this object before components join.
   *
   * @example
   * window.wireview.hooks.ChartHook = {
   *   mounted() {
   *     this.chart = new Chart(this.el, config);
   *   },
   *   updated() {
   *     this.chart.update();
   *   },
   *   destroyed() {
   *     this.chart.destroy();
   *   }
   * };
   *
   * @type {Object<string, HookDefinition>}
   */
  hooks: {},

  /**
   * Forwards a user event to a component
   * @param {HTMLElement} element
   * @param {string} name
   * @param {Object} [args]
   * @param {string} [eventType] - Optional event type for loading class
   */
  send(element, name, args, eventType) {
    args = args || {};

    // Handle _target for LiveComponent @myself targeting
    const targetId = args._target;
    if (targetId) {
      delete args._target; // Don't send _target to server as an arg
    }

    const component_el = /** @type {HTMLElement|null} */ (
      element.closest("[wireview-component]")
    );
    if (component_el === null) return;

    // Use target component if specified (LiveComponent), otherwise use closest
    let componentId = targetId || component_el.id;
    let component = connection.components[componentId];

    if (component !== undefined) {
      // Add loading classes
      element.classList.add("wireview-loading");
      if (eventType) {
        element.classList.add(`wireview-${eventType}-loading`);
      }

      // Handle wire-disabled-with: disable element and replace text
      const disabledWithText = element.getAttribute("wire-disabled-with");
      if (disabledWithText !== null) {
        // Store original text and disabled state
        element._wireOriginalText = element.textContent;
        element._wireOriginalDisabled = element.disabled;
        // Apply disabled state and new text
        element.disabled = true;
        element.textContent = disabledWithText;
      }

      const form = /** @type {HTMLFormElement|null} */ (element.closest("form"));
      const targetEl = targetId ? document.getElementById(targetId) : component_el;
      const formScope = form && targetEl && targetEl.contains(form) ? form : targetEl || component_el;

      // Start timing for profiling
      startEventTiming();

      component.dispatch(name, args, formScope);
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

  // ============================================================================
  // Upload API
  // ============================================================================

  /**
   * Get upload manager for a component.
   * @param {HTMLElement} element - Element within the component
   * @returns {UploadManager|null}
   */
  getUploadManager(element) {
    const component = element.closest("[wireview-component]");
    if (component) {
      return getUploadManager(component.id);
    }
    return null;
  },

  /**
   * Trigger file selection for an upload.
   * @param {HTMLElement} element - Element within the component
   * @param {string} uploadName - Name of the upload field
   */
  selectFiles(element, uploadName) {
    const manager = this.getUploadManager(element);
    if (!manager) return;

    const config = manager.configs[uploadName];
    if (!config) {
      console.warn(`[wireview] Unknown upload: ${uploadName}`);
      return;
    }

    const input = document.createElement("input");
    input.type = "file";
    input.accept = config.accept.join(",");
    input.multiple = config.max_entries > 1;

    input.onchange = () => {
      if (input.files?.length) {
        manager.addFiles(uploadName, input.files);
      }
    };

    input.click();
  },

  /**
   * Add files to an upload field.
   * @param {HTMLElement} element - Element within the component
   * @param {string} uploadName - Name of the upload field
   * @param {FileList|File[]} files - Files to upload
   */
  addFiles(element, uploadName, files) {
    const manager = this.getUploadManager(element);
    if (manager) {
      manager.addFiles(uploadName, files);
    }
  },

  /**
   * Cancel an upload.
   * @param {HTMLElement} element - Element within the component
   * @param {string} uploadName - Name of the upload field
   * @param {string} ref - Upload reference
   */
  cancelUpload(element, uploadName, ref) {
    const manager = this.getUploadManager(element);
    if (manager) {
      manager.cancel(uploadName, ref);
    }
  },

  /**
   * Get preview URL for an image upload.
   * @param {HTMLElement} element - Element within the component
   * @param {string} uploadName - Name of the upload field
   * @param {string} ref - Upload reference
   * @returns {string|null}
   */
  getPreviewUrl(element, uploadName, ref) {
    const manager = this.getUploadManager(element);
    if (manager) {
      return manager.getPreviewUrl(uploadName, ref);
    }
    return null;
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

    /**
     * Enable performance profiling.
     * Logs patch times and round-trip latencies for each event.
     */
    enableProfiling() {
      profilingEnabled = true;
      profilingMetrics = {
        patchTimes: [],
        roundTripTimes: [],
        eventCount: 0,
        lastEventStart: 0,
        startTime: performance.now(),
      };
      console.log(
        "%c[wireview]%c Profiling enabled. Interact with the page, then call wireview.debug.profilingReport() to see results.",
        "color: #7c3aed; font-weight: bold",
        "color: inherit"
      );
    },

    /**
     * Disable performance profiling.
     */
    disableProfiling() {
      profilingEnabled = false;
      console.log(
        "%c[wireview]%c Profiling disabled.",
        "color: #7c3aed; font-weight: bold",
        "color: inherit"
      );
    },

    /**
     * Get a profiling report with statistics.
     * @returns {{
     *   enabled: boolean,
     *   duration: number,
     *   eventCount: number,
     *   patch: { count: number, min: number, max: number, avg: number, median: number },
     *   roundTrip: { count: number, min: number, max: number, avg: number, median: number }
     * }}
     */
    profilingReport() {
      const calcStats = (/** @type {number[]} */ arr) => {
        if (arr.length === 0) {
          return { count: 0, min: 0, max: 0, avg: 0, median: 0 };
        }
        const sorted = [...arr].sort((a, b) => a - b);
        const sum = arr.reduce((a, b) => a + b, 0);
        return {
          count: arr.length,
          min: Math.round(sorted[0] * 100) / 100,
          max: Math.round(sorted[sorted.length - 1] * 100) / 100,
          avg: Math.round((sum / arr.length) * 100) / 100,
          median: Math.round(sorted[Math.floor(sorted.length / 2)] * 100) / 100,
        };
      };

      const duration = profilingEnabled
        ? (performance.now() - profilingMetrics.startTime) / 1000
        : 0;

      const report = {
        enabled: profilingEnabled,
        duration: Math.round(duration * 10) / 10,
        eventCount: profilingMetrics.eventCount,
        patch: calcStats(profilingMetrics.patchTimes),
        roundTrip: calcStats(profilingMetrics.roundTripTimes),
      };

      console.group("%c[wireview] Profiling Report", "color: #059669; font-weight: bold");
      console.log("Profiling enabled:", report.enabled);
      console.log("Duration:", report.duration, "seconds");
      console.log("Events processed:", report.eventCount);
      console.log("");
      console.log("Patch times (DOM morph):");
      console.table(report.patch);
      console.log("");
      console.log("Round-trip times (event → response):");
      console.table(report.roundTrip);
      console.groupEnd();

      return report;
    },
  },

  // ============================================================================
  // Form Feedback API
  // ============================================================================

  /**
   * Form feedback utilities for managing field validation feedback.
   */
  feedback: {
    /**
     * Mark a field as touched (show its feedback).
     * @param {string} fieldName - The name of the field
     */
    touch(fieldName) {
      FeedbackManager.markTouched(fieldName);
    },

    /**
     * Check if a field has been touched.
     * @param {string} fieldName - The name of the field
     * @returns {boolean}
     */
    isTouched(fieldName) {
      return FeedbackManager.touchedFields.has(fieldName);
    },

    /**
     * Get all touched field names.
     * @returns {string[]}
     */
    getTouched() {
      return Array.from(FeedbackManager.touchedFields);
    },

    /**
     * Reset all feedback (hide all feedback elements).
     * Useful after form submission or reset.
     */
    reset() {
      FeedbackManager.reset();
    },
  },

  // ============================================================================
  // DOM Configuration API
  // ============================================================================

  /**
   * DOM morphing configuration.
   */
  dom: {
    /**
     * Set a callback that runs before each element is morphed.
     * Use this to preserve client-side attributes or state during LiveView updates.
     *
     * @param {function(Element, Element): void} callback - Function called with (fromEl, toEl)
     *
     * @example
     * // Preserve data-js-* attributes set by JavaScript
     * wireview.dom.onBeforeElUpdated((fromEl, toEl) => {
     *   for (const attr of fromEl.attributes) {
     *     if (attr.name.startsWith('data-js-')) {
     *       toEl.setAttribute(attr.name, attr.value);
     *     }
     *   }
     * });
     *
     * @example
     * // Preserve Alpine.js state
     * wireview.dom.onBeforeElUpdated((fromEl, toEl) => {
     *   if (fromEl._x_dataStack) {
     *     window.Alpine.clone(fromEl, toEl);
     *   }
     * });
     */
    onBeforeElUpdated(callback) {
      boost.setOnBeforeElUpdated(callback);
    },
  },
};
