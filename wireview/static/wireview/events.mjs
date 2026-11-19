/**
 * Event bindings without inline script (#90, docs/design/csp-event-binding.md).
 *
 * `{% on "keyup.debounce.300.enter" "save" %}` renders the attribute
 * `wire-on-keyup.debounce.300.enter` whose value is JSON: `{h, a, t}` for a
 * server handler (name, arguments, LiveComponent target) or `{js}` for a
 * `JS()` command chain. The bundle listens once per event type at the root
 * and runs the bindings it finds on the way up from the event's target.
 *
 * The modifiers read left to right, each one a condition or an effect before
 * the next, which is what the inline code the server used to generate did:
 * it wrapped them right to left around the call.
 *
 * Pure functions, no DOM: tested with `node --test tests/js/`.
 */

export const BINDING_PREFIX = "wire-on-";

/**
 * Bindings that listen beyond their element (#234, GAP-038). The template
 * writes them as a modifier right after the event -- `keydown.window.esc`,
 * `click.away` -- and the server renders them under a prefix of their own,
 * which an older bundle does not read: it ignores them rather than run
 * `click.away` as a plain click, its opposite.
 *
 * - `window`: the event as the window hears it -- one that bubbled up from
 *   anywhere on the page (a key with nothing focused, a click), or the
 *   window's own (`focus`, `blur`, `resize`). After the element bindings it
 *   passed, and not when one of them stopped it.
 * - `away`: the event outside the element, when the element is shown. Before
 *   anything on the page hears it (the window's capture phase), so a click
 *   that opens a menu does not also close it.
 * @type {Record<"window" | "away", string>}
 */
export const SCOPE_PREFIXES = { window: "wire-window-", away: "wire-away-" };

/** @typedef {"element" | "window" | "away"} Scope */

/**
 * @param {Scope} scope
 * @returns {string}
 */
function prefixOf(scope) {
  return scope === "element" ? BINDING_PREFIX : SCOPE_PREFIXES[scope];
}

/**
 * Whether an attribute's name is a binding of any scope.
 * @param {string} name
 * @returns {boolean}
 */
export function isBindingName(name) {
  return name.startsWith(BINDING_PREFIX) || name.startsWith(SCOPE_PREFIXES.window) || name.startsWith(SCOPE_PREFIXES.away);
}

/**
 * Whether a key belongs to an IME: one it is composing with, or the key that
 * starts a composition (Chromium's first `keydown` says `isComposing: false`
 * and `keyCode` 229). A window binding hears every key on the page, the ones
 * typed into a field included; a page-wide shortcut never takes an IME's.
 * @param {{isComposing?: boolean, keyCode?: number}} event
 * @returns {boolean}
 */
export function belongsToIme(event) {
  return Boolean(event.isComposing) || event.keyCode === 229;
}

/**
 * The events a browser fires on its own when the element with focus is removed
 * or moved: the focus events, and `change` first when the field's value changed
 * since it was focused (Chromium does, as it does when the user leaves).
 */
const REMOVAL_EVENTS = new Set(["blur", "focusout", "focus", "focusin", "change"]);

/**
 * Whether an event is the render's doing rather than the user's. A browser
 * blurs the focused element it removes -- while it is still in the document --
 * so a render that hid a field with `{% on "blur" %}` sent that field's handler
 * with what was typed: Escape that cancelled an edit saved it. A field typed in
 * is sent `change` before the blur, so `{% on "change" %}` did the same. A server
 * change is applied synchronously, so no input of the user's can land inside one,
 * and none of these dispatched during it is.
 * @param {string} type - the event's type
 * @param {boolean} applying - whether a server change is being applied to the DOM right now
 * @returns {boolean}
 */
export function isRenderEcho(type, applying) {
  return applying && REMOVAL_EVENTS.has(type);
}

/** Modifiers that take the next token as their argument. */
const ARITY = { debounce: 1, throttle: 1, key: 1, key_code: 1 };

/** Key shortcuts and the `event.key` (lowercased) each one stands for. */
const KEYS = {
  enter: "enter",
  tab: "tab",
  delete: "delete",
  backspace: "backspace",
  esc: "escape",
  space: " ",
  up: "arrowup",
  down: "arrowdown",
  left: "arrowleft",
  right: "arrowright",
};

/**
 * @typedef {{name: string, arg?: string}} Step
 * @typedef {{type: string, steps: Step[], scope: Scope}} Binding
 */

/**
 * Read a binding attribute's name.
 * @param {string} attribute - e.g. `wire-on-keyup.debounce.300.enter`, `wire-window-keydown.esc`
 * @returns {Binding | null} null when the name is not a binding
 */
export function parseBinding(attribute) {
  /** @type {Scope} */
  let scope;
  if (attribute.startsWith(BINDING_PREFIX)) scope = "element";
  else if (attribute.startsWith(SCOPE_PREFIXES.window)) scope = "window";
  else if (attribute.startsWith(SCOPE_PREFIXES.away)) scope = "away";
  else return null;
  const [type, ...tokens] = attribute.slice(prefixOf(scope).length).split(".");
  if (!type) return null;
  /** @type {Step[]} */
  const steps = [];
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token in KEYS) {
      steps.push({ name: "key", arg: KEYS[/** @type {keyof KEYS} */ (token)] });
    } else if (token in ARITY) {
      steps.push({ name: token, arg: tokens[++i] });
    } else {
      steps.push({ name: token });
    }
  }
  return { type, steps, scope };
}

/**
 * The binding attributes among `names` that answer to an event `type`, in order.
 * @param {string[]} names - an element's attribute names
 * @param {string} type - `event.type`
 * @param {Scope} [scope="element"] - which of them: the element's own, or those listening beyond it
 * @returns {string[]}
 */
export function bindingsFor(names, type, scope = "element") {
  const exact = prefixOf(scope) + type;
  return names.filter((name) => name === exact || name.startsWith(exact + "."));
}

/**
 * Whether an `away` binding on `el` hears an event at `target`: one outside it.
 * @param {{contains(other: any): boolean}} el
 * @param {any} target - the event's target, null when it is not in the document (the window's own)
 * @returns {boolean}
 */
export function isAway(el, target) {
  return target !== null && target !== undefined && !el.contains(target);
}

/**
 * @typedef {object} StepOps
 * @property {() => void} prevent
 * @property {() => void} stop
 * @property {(delay: number, rest: () => void) => void} debounce - run `rest` after `delay`, replacing a pending one
 * @property {(delay: number) => boolean} throttle - whether a call now is allowed
 * @property {() => void} fire - send to the server or run the JS commands
 */

/**
 * Run a binding's modifiers against an event, then fire it.
 *
 * A condition that does not hold stops the run. `debounce` hands the rest of
 * the run to its timer, so a modifier after it runs late: `prevent` there is
 * too late to matter, exactly as it always was.
 * @param {Step[]} steps
 * @param {{key?: string, keyCode?: number, isComposing?: boolean, ctrlKey?: boolean, altKey?: boolean, shiftKey?: boolean, metaKey?: boolean}} event
 * @param {StepOps} ops
 * @param {number} [from=0]
 */
export function runSteps(steps, event, ops, from = 0) {
  for (let i = from; i < steps.length; i++) {
    const { name, arg } = steps[i];
    switch (name) {
      case "prevent":
        ops.prevent();
        break;
      case "stop":
        ops.stop();
        break;
      case "ctrl":
      case "alt":
      case "shift":
      case "meta":
        if (!event[/** @type {"ctrlKey"} */ (`${name}Key`)]) return;
        break;
      case "key":
        // A key an IME is composing with belongs to the IME: the Enter that
        // picks a Hangul syllable is not the Enter that submits.
        if (event.isComposing) return;
        if (String(event.key).toLowerCase() !== String(arg).toLowerCase()) return;
        break;
      case "key_code":
        if (event.isComposing) return;
        if (String(event.keyCode) !== String(arg)) return;
        break;
      case "debounce":
        ops.debounce(Number(arg), () => runSteps(steps, event, ops, i + 1));
        return;
      case "throttle":
        if (!ops.throttle(Number(arg))) return;
        break;
      default:
        // The server refuses an unknown modifier since 1.0; a page an older
        // server drew may still carry one, and the old transpiler ignored it too.
        break;
    }
  }
  ops.fire();
}
