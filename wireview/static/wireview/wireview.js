import ReconnectingWebSocket from "reconnecting-websocket";
import { JOINED_SINCE, JOIN_REFS_SINCE, PROTOCOL_VERSION, REFS_SINCE, applyPartial, buildHtml } from "./rendered.mjs";
import { Joins, settledEvent } from "./joins.mjs";
import { commitScope, isCommitAction } from "./values.mjs";
import { LoadingLedger } from "./loading.mjs";
import { BINDING_PREFIX, bindingsFor, parseBinding, runSteps } from "./events.mjs";
import { planInsert, planTrim } from "./streams.mjs";
import { createDocumentReady } from "./ready.mjs";
import { RELOAD_STORAGE_KEY, shouldReload } from "./reload.mjs";
import { readReconnectSettings, reconnectOptions } from "./reconnect.mjs";
import { NAVIGATED_EVENT, NavigationLog, carriedAcross } from "./navigation.mjs";
import { UploadManagers } from "./uploads.mjs";
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
 * @property {Object|null} diff - Phoenix-style diff, or null when only children changed
 * @property {Object<string, Object|null>} [children] - Diffs of the LiveComponents rendered along with this one, by id
 */

/**
 * @typedef {Object} PhoenixFullDiff
 * @property {string[]} s - Static parts
 * @property {Array<string|Object>} d - Dynamic parts (strings, comprehensions {s, d}, blocks {r, d}, component refs {c})
 * @property {string} f - Fingerprint
 */

/**
 * @typedef {Object<string, string|Object>} PhoenixPartialDiff
 * Partial diff with numeric string keys mapping to new values: a string, a
 * comprehension {s, d}, a comprehension item update {u, n}, a block, a block
 * partial {p}, or a component ref {c}
 */

/**
 * @typedef {Object} DOMPayload
 * @property {string} id - Element ID
 * @property {string} [html] - HTML content
 */

//: Captured here, while this bundle is still inside the deferred phase. Asking
//: later would be too late to tell the two meanings of "interactive" apart --
//: see ready.mjs.
const whenDocumentReady = createDocumentReady(document, window);

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
    /** @type {number} The protocol version this socket's server announced (0 until a join is answered) */
    this.serverVsn = 0;
    /**
     * @type {number} The last ref given to a user event or a join on this
     * connection. One counter, so a render's ref names one of them only.
     */
    this.lastRef = 0;
    /** @type {Joins} which answers are for the join the page holds under an id (#139) */
    this.joins = new Joins();
    /**
     * @type {number} The last ref given to a hook's pushEvent. One counter for
     * the page, not one per component: a reply finds its callback by the ref
     * alone, and two components counting from 1 swapped answers (#108).
     */
    this.lastHookRef = 0;
    /**
     * @type {LoadingLedger<HTMLElement>} which elements show a loading state
     * until which answer (#118)
     */
    this.loading = new LoadingLedger();
    /** @type {number} how many times a socket has opened; a hook manager remembers the one it was live in */
    this.epoch = 0;
    /** @type {NavigationLog} where the last boosted navigation landed, for `wireview:navigated` */
    this.navigations = new NavigationLog(document.location.href);
  }

  /**
   * Opens WebSocket connection to the server.
   * @param {string} [path="__wireview__"] - WebSocket endpoint path
   */
  open(path = "__wireview__") {
    let protocol = location.protocol.replace("http", "ws");
    this.socket = new ReconnectingWebSocket(
      // The diff forms this client applies. Without it the server assumes
      // the oldest, so an older bundle never receives a form it cannot read.
      `${protocol}//${location.host}/${path}?vsn=${PROTOCOL_VERSION}`,
      [],
      {
        maxEnqueuedMessages: 0,
        // The backoff WIREVIEW["RECONNECT_*"] sets (#124): what a rolling
        // deploy's crowd of reconnecting pages does to the servers left
        ...reconnectOptions(readReconnectSettings(document)),
      }
    );

    this.socket.addEventListener("open", () => {
      debugLog("ws", "Connected to server");

      const reconnecting = this.wasConnected;
      this.wasConnected = true;
      this.epoch += 1;

      this.sendQueryString();
      // Held until the document is ready so that every deferred script -- a
      // page's own hook definitions among them -- has run before anything joins.
      whenDocumentReady(() => {
        this.components = {};
        this.joinAllComponents();
        // Flush messages queued while the socket was connecting
        while (this.messageQueue.length) {
          const { command, payload } = this.messageQueue.shift();
          this._send(command, payload);
        }
        // After the joins: the server handles one socket's messages in order,
        // and a recovery event sent before its component's join found nothing
        // to reach and was dropped (#110).
        if (reconnecting) this.recoverForms();
      });
    });

    this.socket.addEventListener("message", (event) =>
      this._processMessage(event)
    );

    this.socket.addEventListener("close", () => {
      // The next socket may reach another server; it announces itself again.
      this.serverVsn = 0;
      // Nothing sent on this socket will be answered.
      boost.valueGuard.clear();
      this.loading.clear().forEach(unmarkLoading);
      debugLog("ws", "Disconnected from server");

      // Notify hooks of disconnection before clearing components
      for (const component of Object.values(this.components)) {
        component.hookManager.disconnected();
      }

      this.components = {};
      // The instances the uploads belonged to ended with the connection (#137),
      // and their requests stop: the server drops the connection's chunks. This
      // runs again for each reconnect that fails; a file chosen since, or before
      // the first connection, belongs to no instance yet and waits for the next.
      uploadManagers.connectionClosed();
      this.joins.clear();
      document.querySelectorAll("[wireview-component]").forEach((el) => {
        const element = /** @type {HTMLElement} */ (el);
        element.classList.add("wireview-disconnected");
        element.dataset.isLive = "false";
      });
    });

    boost.navEvent.addEventListener("newLocation", () => {
      this.sendQueryString();
    });

    boost.navEvent.addEventListener("newContent", (event) => {
      this.joinAllComponents();
      const { token, landed } = /** @type {CustomEvent} */ (event).detail;
      if (landed) this.announceNavigation(token);
    });
  }

  /**
   * Tells the page a boosted navigation has landed, once, after the new page's
   * components have joined (#128): `navigated()` to the hooks it carried over
   * -- a sticky component's among them, which nothing else tells -- and then
   * `wireview:navigated` on `document`.
   * @param {number} token - the navigation that landed
   */
  announceNavigation(token) {
    for (const component of Object.values(this.components)) {
      component.hookManager.navigated(token);
    }
    document.dispatchEvent(
      new CustomEvent(NAVIGATED_EVENT, { detail: this.navigations.landed(document.location.href) })
    );
  }

  /**
   * Starts infinite scroll for a component and the LiveComponents inside it,
   * after the morph already scheduled, so it sees the list as it now is.
   * @param {string} id
   */
  startViewports(id) {
    window.requestAnimationFrame(() => {
      const root = document.getElementById(id);
      if (!root) return;
      for (const el of [root, ...root.querySelectorAll("[wireview-live]")]) {
        this.components[el.id]?.viewportObserver.start();
      }
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
    const elements = Array.from(document.querySelectorAll("[wireview-component]"));
    const onPage = new Set(elements.map((element) => element.id));
    // The ones that left go first: an id a LiveComponent had on the page
    // before can name a root now, and the server takes its join only once the
    // parent's leave has taken the LiveComponent along (#146)
    for (const [id, component] of Object.entries(this.components)) {
      if (onPage.has(id)) continue;
      // Its root left the page, which the hook manager's MutationObserver --
      // watching inside the root -- never sees (#107)
      component.hookManager.destroy();
      delete this.components[id];
      this.joins.forget(id);
      // Nothing it was waiting for is for the page any more (#146)
      this.loading.abandon(id).forEach(unmarkLoading);
      // Its files, requests and previews too: a config it was still owed must
      // not register them later (#137)
      uploadManagers.dispose(id);
      // A LiveComponent is its parent's: the parent's render already retired
      // it, and by the time a leave arrived the id might name the instance
      // shown again since (#140). Its root's leave, if the root went too,
      // takes it along.
      if (!component.owned) this.sendLeave(id);
    }
    for (const element of elements) {
      let component = this.components[element.id];
      if (!component) {
        component = new WireviewComponent(element.id);
        this.components[element.id] = component;
      }
      // What the element is now: new DOM under the id can make a LiveComponent
      // a root, or a root a LiveComponent (#146)
      component.owned = element.hasAttribute("wireview-live");
      component.join();
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
      case "render": {
        // End timing for profiling (event round-trip complete)
        endEventTiming();

        const { id, diff, children, ref, vsn, instances } = payload;
        if (typeof vsn === "number") this.serverVsn = vsn;
        const target = this.components[id];
        // The render answering a join carries vsn, and its ref is the join's
        const eventRef = settledEvent("render", payload);
        // The answer to a committing event may reset the fields it came from,
        // in the morph this render causes and no other (#92). That morph runs
        // now, not on the next frame, so no later render folds into it.
        const permission = typeof eventRef === "number" ? boost.valueGuard.answer(eventRef) : undefined;
        // The loading state an event started ends with its own answer (#118),
        // released before the morph so the server's HTML has the last word on
        // the element's text. A render that answers something else leaves it.
        // An event sent without a ref -- to a server that does not echo them,
        // or before the join said which server this is -- is answered by the
        // next render of its component, except the join's own (it carries
        // vsn), which the server sent before it read the event.
        const released =
          typeof eventRef === "number"
            ? this.loading.answer(eventRef)
            : typeof vsn === "number"
              ? []
              : this.loading.answerUnpaired(id);
        released.forEach(unmarkLoading);
        // Not for a component whose element left the page: the render was on
        // its way when the page let it go, and its LiveComponents went with it
        // (#140). Nor, while the page waits for the answer to a join it sent,
        // for the instance that join replaces (#139): its render would name
        // that instance as current, and paint it over the new one. A
        // LiveComponent's own render is its root's instance's (#146).
        if (!this.joins.render(target?.owned ? rootIdOf(id) : id, ref, Boolean(target))) break;
        // The instances this render is the first of: whose upload configs to
        // take (#137)
        if (instances) uploadManagers.named(instances);
        // Register the children first, before any frame is scheduled: the
        // parent's HTML is built from their renders, and a later diff for a
        // child must find its component whether or not the parent has patched
        // the DOM yet.
        const changedChildren = [];
        for (const [childId, childDiff] of Object.entries(children || {})) {
          let child = this.components[childId];
          if (!child) {
            child = new WireviewComponent(childId, true);
            this.components[childId] = child;
          }
          if (childDiff) {
            child.applyDiffData(childDiff);
            changedChildren.push(child);
          }
        }
        // A server that does not say `joined`: the first render is the best sign
        // the join has landed
        if (this.serverVsn < JOINED_SINCE) this.startViewports(id);
        if (diff) {
          // One patch: the parent's HTML embeds the children's current renders
          target.applyDiffData(diff);
          if (permission && permission.size) {
            target.morphNow(permission);
          } else {
            target.scheduleMorph();
          }
        } else {
          // The parent did not change, so each changed child patches itself
          for (const child of changedChildren) {
            child.scheduleMorph();
          }
        }
        break;
      }
      case "remove": {
        // A halted join's, or its instance's own; not the replaced one's (#146)
        if (!this.joins.about(payload.id, payload.ref)) break;
        document.getElementById(payload.id)?.remove();
        boost.navEvent.sendNewContent();
        break;
      }

      case "error": {
        // Server code raised for this component (#94). The connection lives on.
        const { id, during, ref } = payload;
        // The event is over: its answer will not come as a render, and the
        // fields it came from keep what the user typed.
        const eventRef = settledEvent("error", payload);
        if (eventRef !== undefined) {
          boost.valueGuard.answer(eventRef);
          this.loading.answer(eventRef).forEach(unmarkLoading);
        }
        // About the instance a join the page has since sent replaces: the new
        // one, and the element it is for, are not hurt (#139)
        if (!this.joins.error(id, ref, during)) break;
        const target = this.components[id];
        const element = document.getElementById(id);
        // The instance is gone; nothing it was waiting for will be answered
        this.loading.abandon(id).forEach(unmarkLoading);
        if (during === "event" && target && element) {
          // The instance that raised is gone. The element still carries the
          // state from before the event, so joining with it is the rollback.
          target.rejoin();
        } else if (element) {
          // A join that failed is not retried: it would fail again. The page
          // keeps what the server rendered, and the next connection tries.
          delete this.components[id];
          this.joins.forget(id);
          // No instance, so no uploads: its own nor its LiveComponents'
          uploadManagers.joinFailed(id, liveIdsIn(element));
          element.classList.add("wireview-error");
        }
        element?.dispatchEvent(
          new CustomEvent("wireview:error", { bubbles: true, detail: { id, during } })
        );
        break;
      }
      case "reload":
        // The server refused a signed state (expired, pre-envelope, or invalid)
        // and mounted nothing. Reloading re-renders the page with fresh tokens.
        // A join the page has since replaced is answered on its own (#146).
        if (!this.joins.about(payload.id, payload.ref)) break;
        this._reloadPage(payload.reason);
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
            // params_changed only once the new page is actually on screen. A push
            // that leaves the live_session becomes a full page load, and telling
            // the old connection about the new URL would have it re-render under
            // the policy the navigation was leaving behind (#58).
            boost.HistoryCache.push(url).then((sameSession) => {
              if (!sameSession) return;
              const urlObj = new URL(url, document.location.origin);
              const params = parseQueryString(urlObj.search);
              this.sendParamsChanged(url, params);
            });
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

      case "joined":
        // The join and everything its joined() queued -- a stream's first
        // page -- have arrived: infinite scroll may judge the list now (#112).
        // Not the replaced join's: its list is not the one on the page (#146).
        if (!this.joins.about(payload.id, payload.ref)) break;
        this.startViewports(payload.id);
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
   * Reload the page after the server refused a signed state.
   * Guarded so a page whose fresh state is refused again cannot loop.
   * @param {string} reason - Why the server refused it ("expired", "invalid", "live_session")
   * @private
   */
  _reloadPage(reason) {
    const now = Date.now();
    let last = null;
    try {
      last = window.sessionStorage.getItem(RELOAD_STORAGE_KEY);
    } catch (e) {
      // Storage can be unavailable (private mode, blocked cookies): reload once.
    }
    if (!shouldReload(last, now)) {
      console.warn(
        `[wireview] Server asked for a reload (${reason}) right after the last one; not reloading again`
      );
      return;
    }
    try {
      window.sessionStorage.setItem(RELOAD_STORAGE_KEY, String(now));
    } catch (e) {
      // Without storage the guard cannot arm; the reload still happens.
    }
    debugLog("ws", `Reloading the page (${reason})`);
    window.location.reload();
  }

  /**
   * Handle upload operations from server.
   * @param {Object} payload - Upload operation payload
   * @private
   */
  _handleUploadOp(payload) {
    const { op, upload, ref, id, instance, ...data } = payload;

    // "config" creates the upload on this side, so it names its component:
    // searching for a component that already has the upload finds none.
    if (op === "config" && id) {
      // Only from the instance the page holds under the id. Another's -- one
      // that left, or that a join replaced -- would bring the manager back, or
      // give the old instance's files and settings to the new one (#137).
      uploadManagers.forConfig(id, instance)?.configure(upload, data);
      return;
    }

    // Find the component that owns this upload
    for (const componentId of Object.keys(this.components)) {
      const manager = uploadManagers.find(componentId);
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
            const existing = container.querySelector(`#${CSS.escape(item.id)}`);
            const plan = planInsert({
              exists: existing !== null,
              at,
              childCount: container.children.length,
            });
            switch (plan.mode) {
              case "replace":
                // Same id: a newer version of an item already on screen.
                existing.replaceWith(el);
                break;
              case "prepend":
                container.prepend(el);
                break;
              case "append":
                container.appendChild(el);
                break;
              case "before":
                container.children[plan.index].before(el);
                break;
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

    // Enforce limit by removing excess items from the end new items did not arrive at
    const trim = planTrim({ childCount: container.children.length, limit, at });
    for (let i = 0; i < trim.count; i++) {
      if (trim.fromEnd) {
        container.lastElementChild?.remove();
      } else {
        container.firstElementChild?.remove();
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
   * Gives the server back what a `wire-auto-recover` form holds after a
   * reconnect. The form is still on the page with what the user typed; the
   * server has only the state of the last render, which a signed state
   * restores on join. So once the component has joined again:
   *
   * - `wire-auto-recover="handler"` calls that handler with `form_data`,
   * - a bare `wire-auto-recover` fires the form's change event, so its own
   *   `{% on "change" %}` binding runs -- Phoenix's phx-auto-recover default.
   */
  recoverForms() {
    for (const form of document.querySelectorAll("form[wire-auto-recover]")) {
      const componentEl = form.closest("[wireview-component]");
      if (!componentEl || !this.components[componentEl.id]) continue;

      const handler = form.getAttribute("wire-auto-recover");
      if (handler && handler !== "true") {
        debugLog("form", `Recovering ${componentEl.id} through ${handler}`);
        this._send("user_event", {
          id: componentEl.id,
          command: handler,
          implicit_args: {},
          explicit_args: { form_data: formValues(/** @type {HTMLFormElement} */ (form)) },
        });
      } else {
        form.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  }

  /**
   * Sends a join request for a component.
   * @param {string} name - Component class name
   * @param {string} component_id - Component element ID
   * @param {string} state - Serialized component state
   * @param {Object<string, [string, string]>} children - Child component info
   * @param {number} [ref] - names the join to a server that returns it (#139)
   */
  sendJoin(name, component_id, state, children, ref) {
    debugLog("send", `join ${name}`, { component_id, ref });
    /** @type {{name: string, state: string, children: Object<string, [string, string]>, ref?: number}} */
    const payload = { name, state, children };
    if (ref !== undefined) payload.ref = ref;
    this._send("join", payload);
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
  sendUserEvent(id, command, implicit_args, explicit_args, ref) {
    debugLog("send", `user_event ${command}`, { id, explicit_args, ref });
    /** @type {{id: string, command: string, implicit_args: Object, explicit_args: Object, ref?: number}} */
    const payload = { id, command, implicit_args, explicit_args };
    if (ref !== undefined) payload.ref = ref;
    this._send("user_event", payload);
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
      } else if (!this.wasConnected) {
        // Before the first connection: sent once it opens.
        this.messageQueue.push(message);
      } else {
        // Disconnected after being live. The reconnect joins every component
        // again from the DOM and sends the URL again, so a message for the
        // instances this socket had has nowhere to go (#97).
        debugLog("send", `dropped ${command}: disconnected`, payload);
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
 * HTML for a component reference `{c: id}` inside another component's render.
 * @param {string} id
 * @returns {string}
 */
function resolveComponentHtml(id) {
  const component = connection.components[id];
  return component ? component.currentHtml() : "";
}

/**
 * Represents a client-side wireview component.
 * Manages state synchronization with the server.
 *
 * Keeps the Phoenix LiveView-style render state (static and dynamic parts).
 */
class WireviewComponent {
  /**
   * Creates a new WireviewComponent instance.
   * @param {string} id - The DOM element ID for this component
   * @param {boolean} [owned=false] - a LiveComponent, which its parent owns
   */
  constructor(id, owned = false) {
    /** @type {string} */
    this.id = id;
    /**
     * A LiveComponent: it never joins or leaves on its own; its parent's join
     * and renders carry its lifecycle (docs/design/live-component-ownership.md).
     * @type {boolean}
     */
    this.owned = owned;

    // Phoenix-style state (static/dynamic separation)
    /** @type {string[]|null} */
    this.static = null;
    /** @type {string[]} */
    this.dynamic = [];
    /** @type {string|null} */
    this.fingerprint = null;

    // Hook manager for JavaScript hooks. The hook instances belong to the element
    // (it carries their ids), so a component made for an element that already has
    // a manager -- after a reconnect, the old component is gone -- takes that one
    // over. A fresh manager would neither mount them again nor reach them: no
    // updated, no handleEvent, no pushEvent reply, no reconnected (#110).
    const prior = /** @type {HTMLElement & {__wireviewHookManager?: HookManager}} */ (
      document.getElementById(id) ?? {}
    ).__wireviewHookManager;
    /** @type {HookManager} */
    this.hookManager = prior ?? new HookManager(this);
    this.hookManager.component = this;

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
   * Applies a diff from the server and patches the DOM on the next frame.
   * @param {Object} diff - Phoenix-style diff
   */
  applyDiff(diff) {
    this.applyDiffData(diff);
    this.scheduleMorph();
  }

  /**
   * Folds a diff into this component's render state without touching the DOM.
   * Runs when the message arrives, so diffs for one component always apply in
   * the order the server sent them, whether they came alone or inside a
   * parent's `children`.
   * @param {Object} diff - Phoenix-style diff
   */
  applyDiffData(diff) {
    this.applyPhoenixDiff(diff);
  }

  /**
   * This component's full HTML from its current render state. Component
   * references resolve to the referenced component's current HTML, so a
   * parent's HTML always embeds what its children have right now.
   * @returns {string}
   */
  currentHtml() {
    if (this.static) {
      return buildHtml(this.static, this.dynamic, resolveComponentHtml);
    }
    // Fallback: the element as it stands
    return this.getElemenet()?.outerHTML || "";
  }

  /**
   * Patches this component's element with its current HTML on the next frame.
   * Nothing happens when the element is not in the DOM yet; a parent patch
   * that embeds this component will place it.
   */
  scheduleMorph() {
    window.requestAnimationFrame(() => this.morphNow());
  }

  /**
   * Patches this component's element with its current HTML now.
   * @param {Map<Element, string>} [permission] - fields the render being applied
   *   answers, which may take the server's value (ValueGuard.answer)
   */
  morphNow(permission) {
    let el = this.getElemenet();
    if (el) {
      const html = this.currentHtml();

      if (html) {
        // Call beforeUpdate on all hooks
        this.hookManager.beforeUpdate();

        // Profile patch time
        const patchStart = profilingEnabled ? performance.now() : 0;
        boost.morph(el, html, { permission });
        // The server's HTML has no loading classes: put back the marks of the
        // events still waiting for their answer (#118)
        for (const mark of connection.loading.pending()) {
          for (const marked of mark.elements) {
            if (el.contains(marked)) markLoading(marked, mark.eventType, true);
          }
        }
        if (profilingEnabled) {
          recordPatchTime(performance.now() - patchStart);
        }
        boost.navEvent.sendNewContent();

        // A render that arrived before a disconnect and is painted after it: the
        // server's HTML has no idea the page is offline
        if (!connection.isOpen) {
          el.classList.add("wireview-disconnected");
          el.dataset.isLive = "false";
        }

        // Call updated on all hooks (and scan for new ones)
        this.hookManager.updated();

        // Update viewport observer (scan for new viewport elements)
        this.viewportObserver.updated();

        // Update upload previews (populate src for new preview elements)
        const uploadManager = uploadManagers.find(this.id);
        if (uploadManager) {
          uploadManager.updatePreviews();
        }

        // Update form feedback (manage wire-no-feedback classes)
        FeedbackManager.updated();
      }
    }
  }

  /**
   * Apply Phoenix-style diff (static/dynamic separation) to the render state.
   * @param {PhoenixFullDiff|PhoenixPartialDiff} diff - Phoenix diff object
   */
  applyPhoenixDiff(diff) {
    if ("s" in diff) {
      // Full render: store static parts and dynamic values
      this.static = diff.s;
      this.dynamic = diff.d.slice(); // Clone to avoid mutation
      this.fingerprint = diff.f;
    } else {
      // Partial update: strings, comprehensions, item updates, blocks, refs
      applyPartial(this.dynamic, diff);
    }
  }

  /**
   * Joins this component to the server.
   * Only joins if the component is not already live and its parent is live.
   * A LiveComponent (`wireview-live`) never joins on its own: its parent's
   * join carries its state and its lifecycle runs with the parent's render.
   */
  join() {
    // Only on an open socket. A morph queued before a disconnect can run after it,
    // and its newContent re-runs joinAllComponents: joining then marked the element
    // live and sent a join nowhere, and the reconnect skipped the element as
    // already live -- no join, events dropped, hooks never told (#110). The open
    // handler joins everything once the socket is back.
    if (!connection.isOpen) return;
    const element = /** @type {HTMLElement|null} */ (this.getElemenet());
    if (element && element.dataset.isLive === "false") {
      const parentEl = element?.parentElement?.closest("[wireview-component]");
      const parent = /** @type {HTMLElement|null} */ (parentEl);
      if (!parent || parent.dataset.isLive === "true") {
        element.dataset.isLive = "true";
        // The viewport observer starts once the join has landed (`joined`), not
        // here: the list it judges is still empty until then (#112)
        if (element.hasAttribute("wireview-live")) {
          this.hookManager.init();
          return;
        }
        this.sendJoin(element);

        // Initialize hooks after joining
        this.hookManager.init();
      }
    }
  }

  /**
   * Joins this component again after the server discarded it (`error` during
   * an event, #94). The element and its hooks stay; only the render state is
   * dropped, since the join's answer is a full render.
   */
  rejoin() {
    const element = /** @type {HTMLElement|null} */ (this.getElemenet());
    if (!element) return;
    // A render that arrived before the error may still wait for its frame;
    // its state is the one to join with.
    this.morphNow();
    this.static = null;
    this.dynamic = [];
    this.fingerprint = null;
    this.sendJoin(element);
  }

  /**
   * Sends a join with the element's signed state and its nested components'.
   * @param {HTMLElement} element
   */
  sendJoin(element) {
    // A second join on this connection replaces the instance: new DOM from a
    // boosted navigation, or the rollback after a crash. The server retires the
    // old one with its LiveComponents and their uploads, and so does the page:
    // a file chosen for the old instance is not the new one's (#137).
    // The answer names the new instances; a config from the old ones, still on
    // its way, matches none of them. Named, the join is told from the one it
    // replaces by its answer, which carries the ref (#139).
    const ref = connection.serverVsn >= JOIN_REFS_SINCE ? ++connection.lastRef : undefined;
    uploadManagers.joining(this.id, liveIdsIn(element), connection.joins.sent(this.id, ref));
    // A join that failed before is tried again on a new connection
    element.classList.remove("wireview-error");
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
      children,
      ref
    );
  }

  /**
   * Dispatches a command to this component and sends it to the backend
   * @param {string} command - Event command name
   * @param {Object} args - Explicit arguments
   * @param {HTMLElement} formScope - Form or component element to serialize
   * @param {Element|null} [commitFrom] - the element of a committing action (values.mjs)
   * @param {{elements: HTMLElement[], eventType?: string}} [loading] - what markLoading marked
   */
  dispatch(command, args, formScope, commitFrom = null, loading = undefined) {
    const fields = commitFrom ? commitScope(commitFrom, formScope) : new Map();
    // Paired with its answer by ref when this server echoes refs: the answer
    // may reset the fields a committing action sends (#92), and ends the
    // loading state the event started (#118).
    /** @type {number|undefined} */
    const ref =
      (fields.size || loading?.elements.length) && connection.serverVsn >= REFS_SINCE ? ++connection.lastRef : undefined;
    if (fields.size) boost.valueGuard.record(ref ?? null, fields);
    if (loading) connection.loading.track(ref ?? null, this.id, loading.elements, loading.eventType);
    connection.sendUserEvent(this.id, command, this.serialize(formScope), args, ref);
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
 * @property {function(): void} [navigated] - Called after a boosted navigation the hook
 *   stayed on the page through (a sticky component's, #128)
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
    /** @private the navigation current when it mounted (navigation.mjs) */
    this.__navigation = boost.navigationToken();
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
    /** @type {MutationObserver|null} */
    this.observer = null;
    /** @type {number|null} the connection epoch this manager was last initialized in */
    this.epoch = null;
  }

  /**
   * Initialize hooks after component joins.
   */
  init() {
    const root = /** @type {(HTMLElement & {__wireviewHookManager?: HookManager}) | null} */ (
      this.component.getElemenet()
    );
    if (root) root.__wireviewHookManager = this;
    this.scanAndMount();
    this.setupMutationObserver();
    // Live before, on an earlier socket: this join is a reconnect
    if (this.epoch !== null && this.epoch !== connection.epoch) this.reconnected();
    this.epoch = connection.epoch;
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
      // A nested component's hooks are its own. The parent joins first, and a
      // hook it took was one the nested component's push_event never reached (#107).
      if (element.closest("[wireview-component]") !== root) return;
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
    // A HookManager adopted after a reconnect is initialized again
    this.observer?.disconnect();

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
    // A morph that moves an element (idiomorph matches rows by id) removes and
    // inserts it, and this runs after the morph: an element back in the document
    // was moved, not removed, and its hook lives on.
    if (node.isConnected) return;
    const elements = [node, ...(node.querySelectorAll?.("[wire-hook]") ?? [])];
    for (const el of elements) {
      const element = /** @type {HTMLElement} */ (el);
      for (const hookId of [...(element.__wireviewHookIds ?? [])]) {
        this.destroyHook(hookId);
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
   * Called after a boosted navigation landed: the hooks that were here before it.
   * @param {number} token - the navigation
   */
  navigated(token) {
    const entries = [...this.instances.values()].map((context) => ({
      navigation: context.__navigation,
      connected: context.el.isConnected,
      hook: context,
    }));
    for (const context of carriedAcross(entries, token)) {
      if (context.navigated) {
        try {
          context.navigated();
        } catch (e) {
          console.error(`[wireview] Error in ${context.__hookName}.navigated():`, e);
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
    const ref = callback ? `hook-${++connection.lastHookRef}` : null;

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
   * Cleanup all hooks: the component left the page.
   */
  destroy() {
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
    for (const hookId of [...this.instances.keys()]) {
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
    /** @type {boolean} whether the join has landed and bindings are watched (#112) */
    this.started = false;
  }

  /**
   * Start watching the viewport bindings. Once the join has landed: before
   * that a stream's items are not on the page, the bottom binding sits near
   * the top, and its first callback asked for a second page before the first
   * arrived (#112).
   */
  start() {
    if (this.started) return;
    this.started = true;
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
    if (!this.started) return;
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

/** @type {UploadManagers<UploadManager>} */
const uploadManagers = new UploadManagers((id) => new UploadManager(id));

/**
 * The ids of the LiveComponents inside a component's element.
 * @param {Element} element
 * @returns {string[]}
 */
function liveIdsIn(element) {
  return Array.from(element.querySelectorAll("[wireview-live]"), (live) => live.id);
}

/**
 * The root component a LiveComponent's element sits in: the one whose join
 * made its instance (#146).
 * @param {string} id
 * @returns {string} the root's id, or `id` when its element is not on the page
 */
function rootIdOf(id) {
  return document.getElementById(id)?.closest("[wireview-component]:not([wireview-live])")?.id ?? id;
}

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
    /**
     * Files chosen before their upload's config arrived, by upload name.
     * @type {Object<string, File[]>}
     */
    this.waiting = {};
    /**
     * The preview blob URLs this manager made. Kept here as well as on the
     * img: the img may have left the page by the time the manager ends.
     * @type {Set<string>}
     */
    this.previewUrls = new Set();
  }

  /**
   * Ends this manager with its component's instance (uploads.mjs): the files
   * still waiting for a config, the requests in flight and the preview URLs.
   * Nothing is sent to the server, which ends the instance's uploads itself.
   */
  dispose() {
    this.waiting = {};
    for (const entries of Object.values(this.entries)) {
      for (const entry of Object.values(entries)) {
        entry.status = "cancelled";
        entry.controller?.abort();
      }
    }
    this.entries = {};
    this.configs = {};
    this.previewUrls.forEach((url) => URL.revokeObjectURL(url));
    this.previewUrls.clear();
    debugLog("upload", `Disposed uploads of ${this.componentId}`);
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
    const waiting = this.waiting[name];
    if (waiting) {
      delete this.waiting[name];
      this.addFiles(name, waiting);
    }
  }

  /**
   * Add files to an upload field.
   * @param {string} name - Upload field name
   * @param {FileList|File[]} files - Files to upload
   */
  addFiles(name, files) {
    const config = this.configs[name];
    if (!config) {
      // The config follows the render that makes the page live, one channel-layer
      // trip behind it. A file chosen in between was dropped with only a console
      // error, and the upload never started (#137). It waits for the config now.
      this.waiting[name] = [...(this.waiting[name] || []), ...Array.from(files)];
      debugLog("upload", `No config yet for upload ${name}; holding ${files.length} file(s)`);
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
          this.previewUrls.add(url);
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
        this.previewUrls.delete(img._blobUrl);
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
        // The component's instance ended while the chunk was on its way
        if (entry.status === "cancelled") break;
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
      // Revoked with the manager, like the preview tags' own
      const url = URL.createObjectURL(entry.file);
      this.previewUrls.add(url);
      return url;
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
   * Dispatch an upload event on the component element.
   *
   * `upload:progress` goes out as `wireview:upload-progress`: the public events
   * are the `wireview:` ones, and a bare `upload:` could be anyone's. The 0.x
   * name goes out too until 2.0 (#119).
   *
   * @param {string} eventName - the 0.x name, `upload:<what>`
   * @param {Object} detail
   * @private
   */
  _dispatchEvent(eventName, detail) {
    const el = document.getElementById(this.componentId);
    if (el) {
      const init = { detail: { ...detail, componentId: this.componentId }, bubbles: true };
      el.dispatchEvent(new CustomEvent(`wireview:${eventName.replace(":", "-")}`, init));
      el.dispatchEvent(new CustomEvent(eventName, init));
    }
  }
}

/**
 * Get or create upload manager for a component.
 * @param {string} componentId
 * @returns {UploadManager}
 */
function getUploadManager(componentId) {
  return uploadManagers.get(componentId);
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

    // A submit shows every field's feedback, touched or not: the user asked for
    // the verdict on the whole form. Phoenix does the same for phx-feedback-for.
    document.addEventListener("submit", (e) => {
      if (!(e.target instanceof HTMLFormElement)) return;
      for (const field of e.target.elements) {
        const el = /** @type {HTMLElement} */ (field);
        const name = this.isFormInput(el) ? this.getFieldName(el) : null;
        if (name) this.markTouched(name);
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

/**
 * A form's values as a plain object; a name with several values maps to a list.
 * @param {HTMLFormElement} form
 * @returns {Object<string, FormDataEntryValue | FormDataEntryValue[]>}
 */
function formValues(form) {
  /** @type {Object<string, FormDataEntryValue | FormDataEntryValue[]>} */
  const data = {};
  for (const [name, value] of new FormData(form).entries()) {
    const seen = data[name];
    if (seen === undefined) data[name] = value;
    else if (Array.isArray(seen)) seen.push(value);
    else data[name] = [seen, value];
  }
  return data;
}

/**
 * Whether `el` submits its form when clicked: a click on it is a committing
 * action even when a `click.prevent` binding handles the save (#92).
 * @param {Element} el
 * @returns {boolean}
 */
function isSubmitter(el) {
  if (el instanceof HTMLButtonElement) return el.type === "submit" && el.form !== null;
  if (el instanceof HTMLInputElement) return (el.type === "submit" || el.type === "image") && el.form !== null;
  return false;
}

/**
 * Show that an event is on its way to the server, until its answer arrives
 * (`LoadingLedger`, #118): the loading classes, and `wire-disabled-with`.
 *
 * A submit is marked on its submit buttons too, since that is where
 * `wire-disabled-with` goes in a form (docs/features/optimistic-ui.md). Every
 * path that sends an event for an element calls this -- a binding with a
 * handler and a JS() chain that pushes alike -- or the feedback depends on how
 * the event was written.
 * @param {HTMLElement} element - the element that carries the binding
 * @param {string} [eventType]
 * @param {boolean} [again] - a morph just rewrote the element from the server's
 *   HTML: what it shows now is what to restore once the answer comes
 * @returns {HTMLElement[]} the elements marked, for the ledger
 */
function markLoading(element, eventType, again = false) {
  const marked = [element];
  if (!again && eventType === "submit" && element instanceof HTMLFormElement) {
    for (const button of element.querySelectorAll("[wire-disabled-with]")) {
      if (isSubmitter(button)) marked.push(/** @type {HTMLElement} */ (button));
    }
  }
  for (const el of marked) {
    el.classList.add("wireview-loading");
    if (eventType) el.classList.add(`wireview-${eventType}-loading`);

    const disabledWithText = el.getAttribute("wire-disabled-with");
    if (disabledWithText !== null && (again || el._wireOriginalText === undefined)) {
      if (!again || el.textContent !== disabledWithText) {
        el._wireOriginalText = el.textContent;
        el._wireOriginalDisabled = /** @type {HTMLButtonElement} */ (el).disabled;
      }
      /** @type {HTMLButtonElement} */ (el).disabled = true;
      el.textContent = disabledWithText;
    }
  }
  return marked;
}

/**
 * End the loading state `markLoading` started on `el`.
 * @param {HTMLElement} el
 */
function unmarkLoading(el) {
  for (const name of [...el.classList]) {
    if (name === "wireview-loading" || (name.startsWith("wireview-") && name.endsWith("-loading"))) {
      el.classList.remove(name);
    }
  }
  if (el._wireOriginalText !== undefined) {
    el.textContent = el._wireOriginalText;
    /** @type {HTMLButtonElement} */ (el).disabled = el._wireOriginalDisabled || false;
    delete el._wireOriginalText;
    delete el._wireOriginalDisabled;
  }
}

// ============================================================================
// Event bindings: delegated, no inline script (#90)
// ============================================================================

/**
 * `{% on %}` renders `wire-on-<event>[.<modifier>...]="<json>"` instead of an
 * inline `on<event>` attribute, which a Content Security Policy blocks.
 * Listeners live on `<html>`: before `document`, where boost intercepts link
 * clicks, so a `.prevent` still lands first. docs/design/csp-event-binding.md.
 */
const EventBindings = {
  /** @type {Set<string>} */
  listened: new Set(),
  /** @type {Map<string, import("./events.mjs").Binding | null>} */
  parsed: new Map(),
  /** @type {WeakMap<Element, Map<string, {timer?: ReturnType<typeof setTimeout>, last?: number}>>} */
  state: new WeakMap(),

  init() {
    // The upload tags bind through their own attributes (wire-upload, wire-upload-select).
    this.listen("click");
    this.listen("change");
    this.scan(document.documentElement);
    new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === "attributes") {
          if (record.attributeName?.startsWith(BINDING_PREFIX)) this.learn(record.attributeName);
        } else {
          record.addedNodes.forEach((node) => {
            if (node.nodeType === Node.ELEMENT_NODE) this.scan(/** @type {Element} */ (node));
          });
        }
      }
    }).observe(document.documentElement, { subtree: true, childList: true, attributes: true });
  },

  /**
   * Listen for every event type a binding under `root` names.
   * @param {Element} root
   */
  scan(root) {
    for (const el of [root, ...root.querySelectorAll("*")]) {
      for (const name of el.getAttributeNames()) {
        if (name.startsWith(BINDING_PREFIX)) this.learn(name);
      }
    }
  },

  /** @param {string} attribute */
  learn(attribute) {
    const binding = this.binding(attribute);
    if (binding) this.listen(binding.type);
  },

  /** @param {string} attribute */
  binding(attribute) {
    let binding = this.parsed.get(attribute);
    if (binding === undefined) {
      binding = parseBinding(attribute);
      this.parsed.set(attribute, binding);
    }
    return binding;
  },

  /** @param {string} type */
  listen(type) {
    if (this.listened.has(type)) return;
    this.listened.add(type);
    const root = document.documentElement;
    // A bubbling event is handled on its way up, target first, like the inline
    // handlers were. One that does not bubble (focus, mouseenter) still passes
    // through the root while capturing; only its target's bindings apply.
    root.addEventListener(type, (event) => {
      if (event.bubbles) this.bubble(event);
    });
    root.addEventListener(
      type,
      (event) => {
        if (!event.bubbles && event.target instanceof Element) this.run(event.target, event);
      },
      true
    );
  },

  /** @param {Event} event */
  bubble(event) {
    const start = event.target instanceof Element ? event.target : /** @type {Node|null} */ (event.target)?.parentElement;
    for (let el = start; el; el = el.parentElement) {
      if (this.run(el, event)) break;
    }
  },

  /**
   * Run `el`'s bindings for `event`.
   * @param {Element} el
   * @param {Event} event
   * @returns {boolean} whether a binding stopped propagation
   */
  run(el, event) {
    let stopped = false;
    this.runUpload(el, event);
    for (const attribute of bindingsFor(el.getAttributeNames(), event.type)) {
      const binding = this.binding(attribute);
      if (!binding) continue;
      /** @type {{h?: string, a?: Object, t?: string, js?: JSCommand[]}} */
      let value;
      try {
        value = JSON.parse(el.getAttribute(attribute) || "{}");
      } catch (error) {
        console.error(`[wireview] unreadable binding ${attribute}`, error);
        continue;
      }
      // A server handler runs only on a live component. Before the page
      // was ever live nothing happens, not even .prevent: the form submits to
      // its action and the link navigates, which is what the page does
      // without JavaScript. A page that was live and lost its connection
      // keeps .prevent and .stop and sends nothing: Enter in a form must not
      // reload the page over a dropped socket (#97).
      if (value.h !== undefined && !this.isLive(el, value.t)) {
        if (connection.wasConnected) {
          runSteps(binding.steps, /** @type {KeyboardEvent} */ (event), {
            prevent: () => event.preventDefault(),
            stop: () => {
              stopped = true;
              event.stopPropagation();
            },
            debounce: () => {},
            throttle: () => true,
            fire: () => {},
          });
        }
        continue;
      }
      const element = /** @type {HTMLElement} */ (el);
      runSteps(binding.steps, /** @type {KeyboardEvent} */ (event), {
        prevent: () => event.preventDefault(),
        stop: () => {
          stopped = true;
          event.stopPropagation();
        },
        debounce: (delay, rest) => {
          const state = this.stateOf(el, attribute);
          clearTimeout(state.timer);
          state.timer = setTimeout(rest, delay);
        },
        throttle: (delay) => {
          const state = this.stateOf(el, attribute);
          const now = Date.now();
          if (state.last !== undefined && now - state.last < delay) return false;
          state.last = now;
          return true;
        },
        fire: () => {
          // A debounce may end after the connection did
          if (value.h !== undefined && !this.isLive(el, value.t)) return;
          // A submit, a change, leaving the field or Enter commits the fields
          // it comes from: its answer may reset them (a todo input emptied
          // after Enter). Anything else leaves them to the user (#91, #92).
          const commit = isCommitAction(event.type, binding.steps, { submitter: isSubmitter(element) });
          if (value.js) {
            window.wireview.exec(element, value.js, { commit, eventType: event.type });
          } else if (value.h !== undefined) {
            window.wireview.send(element, value.h, { ...(value.a || {}) }, { eventType: event.type, commit, target: value.t });
          }
        },
      });
    }
    return stopped;
  },

  /**
   * The upload tags' two actions: files chosen in an upload input, and a
   * button that opens the file picker.
   * @param {Element} el
   * @param {Event} event
   */
  runUpload(el, event) {
    if (event.type === "change" && el instanceof HTMLInputElement && el.hasAttribute("wire-upload")) {
      if (el.files?.length) window.wireview.addFiles(el, /** @type {string} */ (el.getAttribute("wire-upload")), el.files);
    } else if (event.type === "click" && el.hasAttribute("wire-upload-select")) {
      window.wireview.selectFiles(/** @type {HTMLElement} */ (el), /** @type {string} */ (el.getAttribute("wire-upload-select")));
    }
  },

  /**
   * Debounce and throttle state, one per element and binding. It used to be
   * one timer for the whole page, so two debounced inputs cancelled each other.
   * @param {Element} el
   * @param {string} attribute
   */
  stateOf(el, attribute) {
    let perElement = this.state.get(el);
    if (!perElement) {
      perElement = new Map();
      this.state.set(el, perElement);
    }
    let state = perElement.get(attribute);
    if (!state) {
      state = {};
      perElement.set(attribute, state);
    }
    return state;
  },

  /**
   * Whether the component a server binding belongs to can take the event.
   * @param {Element} el
   * @param {string} [target] - a LiveComponent id (`myself`)
   */
  isLive(el, target) {
    const componentEl = /** @type {HTMLElement|null} */ (
      target ? document.getElementById(target) : el.closest("[wireview-component]")
    );
    return Boolean(
      connection.isOpen && componentEl && componentEl.dataset.isLive === "true" && connection.components[componentEl.id]
    );
  },
};

EventBindings.init();

connection.open();

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
 * @param {{commit?: boolean, eventType?: string}} [options] - a `push` commits the fields it comes
 *   from (#92) and marks its element loading for `eventType`
 * @returns {Promise<void>}
 */
async function executeCommand(cmd, element, options = {}) {
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
            const marked = markLoading(element, options.eventType);
            // A push inside a committing binding commits like the binding would (#92).
            component.dispatch(cmd.event, cmd.value || {}, formScope, options.commit ? element : null, {
              elements: marked,
              eventType: options.eventType,
            });
          }
        }
      }
      break;

    // Browser commands
    case "navigate":
      if (cmd.url) {
        // replace=True still navigates; it only takes this history entry's place.
        boost.HistoryCache.load(cmd.url, { replace: Boolean(cmd.replace) });
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
   * Go to a URL the way a boosted link does: in place when `BOOST_PAGES` is on
   * and the URL is this site's, otherwise an ordinary page load (#103).
   * @param {string} url
   * @param {{replace?: boolean}} [options] - take the current history entry's place
   * @returns {Promise<boolean>} false when a full page load took over
   */
  visit(url, options = {}) {
    return boost.HistoryCache.load(url, options);
  },

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
   * Forwards a user event to a component.
   *
   * The options were a positional `eventType` then an object, with the target
   * hidden in `args._target`; one object leaves room to add more (#119).
   *
   * @param {HTMLElement} element
   * @param {string} name
   * @param {Object} [args]
   * @param {{eventType?: string, commit?: boolean, target?: string}} [options] -
   *   `eventType` picks the loading class; `commit` says whether the event commits
   *   the fields it comes from (their answer may reset them), by default decided
   *   from `eventType`; `target` is the id of the LiveComponent to send to
   */
  send(element, name, args = {}, options = {}) {
    const { eventType, target: targetId } = options;
    const commit = options.commit ?? isCommitAction(eventType, [], { submitter: isSubmitter(element) });

    const component_el = /** @type {HTMLElement|null} */ (
      element.closest("[wireview-component]")
    );
    if (component_el === null) return;

    // Use target component if specified (LiveComponent), otherwise use closest
    let componentId = targetId || component_el.id;
    let component = connection.components[componentId];

    if (component !== undefined) {
      const marked = markLoading(element, eventType);

      const form = /** @type {HTMLFormElement|null} */ (element.closest("form"));
      const targetEl = targetId ? document.getElementById(targetId) : component_el;
      const formScope = form && targetEl && targetEl.contains(form) ? form : targetEl || component_el;

      // Start timing for profiling
      startEventTiming();

      component.dispatch(name, args, formScope, commit ? element : null, { elements: marked, eventType });
    }
  },

  /**
   * Execute an array of JS commands.
   * Commands are executed sequentially in the order provided.
   * @param {HTMLElement} element - Context element (typically event.target)
   * @param {JSCommand[]} commands - Array of commands to execute
   * @param {{commit?: boolean, eventType?: string}} [options] - whether a `push` in the chain
   *   commits its fields (#92), and the event that started the chain
   * @returns {Promise<void>}
   */
  async exec(element, commands, options = {}) {
    for (const cmd of commands) {
      await executeCommand(cmd, element, options);
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

    // The picker opens now, inside the click: a browser opens one only while
    // the user's activation lasts, so waiting for a config that has not arrived
    // yet would open nothing. Without the config there is no accept filter and
    // one file at a time; what is chosen waits for the config like any file
    // (#137), and is checked against it then.
    const config = manager.configs[uploadName];
    const input = document.createElement("input");
    input.type = "file";
    if (config) {
      input.accept = config.accept.join(",");
      input.multiple = config.max_entries > 1;
    }

    input.onchange = () => {
      // The manager at the time of the choice: the one the click saw may have
      // ended with its instance while the picker was open. A button that left
      // the page has no instance to give the files to.
      if (input.files?.length && element.isConnected) {
        this.getUploadManager(element)?.addFiles(uploadName, input.files);
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
     * Add a callback that runs before each element is morphed.
     * Use this to preserve client-side attributes or state during LiveView updates.
     * Every added callback runs, in the order added; the returned function
     * removes this one.
     *
     * @param {function(Element, Element): void} callback - Function called with (fromEl, toEl)
     * @returns {() => void}
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
      return boost.addBeforeElUpdated(callback);
    },
  },
};
