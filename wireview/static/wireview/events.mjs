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
 * @typedef {{type: string, steps: Step[]}} Binding
 */

/**
 * Read a binding attribute's name.
 * @param {string} attribute - e.g. `wire-on-keyup.debounce.300.enter`
 * @returns {Binding | null} null when the name is not a binding
 */
export function parseBinding(attribute) {
  if (!attribute.startsWith(BINDING_PREFIX)) return null;
  const [type, ...tokens] = attribute.slice(BINDING_PREFIX.length).split(".");
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
  return { type, steps };
}

/**
 * The binding attributes among `names` that answer to an event `type`, in order.
 * @param {string[]} names - an element's attribute names
 * @param {string} type - `event.type`
 * @returns {string[]}
 */
export function bindingsFor(names, type) {
  const exact = BINDING_PREFIX + type;
  return names.filter((name) => name === exact || name.startsWith(exact + "."));
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
 * @param {{key?: string, keyCode?: number, ctrlKey?: boolean, altKey?: boolean, shiftKey?: boolean, metaKey?: boolean}} event
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
        if (String(event.key).toLowerCase() !== String(arg).toLowerCase()) return;
        break;
      case "key_code":
        if (String(event.keyCode) !== String(arg)) return;
        break;
      case "debounce":
        ops.debounce(Number(arg), () => runSteps(steps, event, ops, i + 1));
        return;
      case "throttle":
        if (!ops.throttle(Number(arg))) return;
        break;
      default:
        // Unknown modifiers were ignored by the old transpiler too.
        break;
    }
  }
  ops.fire();
}
