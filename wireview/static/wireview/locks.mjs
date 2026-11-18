/**
 * A form's controls locked while it is submitted (#246).
 *
 * Phoenix LiveView locks a form that `phx-submit` sent until the server
 * answers: its inputs read-only, its buttons disabled. So does wireview: the
 * submit's event is on its way with what the fields said, and an edit made
 * meanwhile would be overwritten by the answer -- or a second click would save
 * twice. A form the server asked to submit to its action (`wire.submit_form`)
 * stays locked until the page leaves.
 *
 * Which control takes which lock is `lockKind`: a text-like input and a
 * textarea `readonly`, a button `disabled`. A checkbox, a radio, a select or a
 * file input has no read-only state the browser honours and is left alone, as
 * Phoenix leaves it.
 *
 * A lock is an attribute, recorded against its element with what the element
 * had before (`original`) and who holds it: an event's loading state, a native
 * submission. It goes when the last holder lets go, and the element gets back
 * what it had -- a field that was read-only before the submit stays read-only.
 *
 * A render morphs the element meanwhile: the server's HTML has no lock. The
 * morph asks `overlay` first, which takes what the server's HTML says as the
 * element's own state from now on and puts the lock on the incoming HTML, so
 * the element stays locked and comes back to what the server last drew. A
 * boosted navigation's morph `forget`s the record instead: the element it
 * pairs is the next page's.
 *
 * Only `getAttribute`, `hasAttribute`, `setAttribute`, `removeAttribute`,
 * `tagName` and `type` are used: tested with `node --test tests/js/`.
 */

/** Input types whose `readonly` the browser honours. */
const READ_ONLY_TYPES = new Set([
  "text",
  "search",
  "url",
  "tel",
  "email",
  "password",
  "number",
  "date",
  "month",
  "week",
  "time",
  "datetime-local",
]);

/** Input types that are buttons. */
const BUTTON_TYPES = new Set(["submit", "image", "reset", "button"]);

/**
 * The attribute that locks `el`, or null when nothing does.
 * @param {{ tagName: string, type?: string }} el
 * @returns {"readonly" | "disabled" | null}
 */
export function lockKind(el) {
  const tag = el.tagName.toUpperCase();
  if (tag === "TEXTAREA") return "readonly";
  if (tag === "BUTTON") return "disabled";
  if (tag !== "INPUT") return null;
  const type = (el.type || "text").toLowerCase();
  if (BUTTON_TYPES.has(type)) return "disabled";
  return READ_ONLY_TYPES.has(type) ? "readonly" : null;
}

/**
 * @typedef {{ getAttribute(name: string): string | null, hasAttribute(name: string): boolean,
 *   setAttribute(name: string, value: string): void, removeAttribute(name: string): void }} Attributed
 * @typedef {{ attribute: string, original: boolean, holders: Set<string> }} Lock
 */

/** @template {Attributed} E */
export class Locks {
  constructor() {
    /** @type {WeakMap<E, Lock>} */
    this.held = new WeakMap();
  }

  /**
   * Lock `el` with `attribute` for `holder`. A second holder shares the lock:
   * what the element had stays the one recorded first.
   * @param {E} el
   * @param {"readonly" | "disabled"} attribute
   * @param {string} holder
   */
  lock(el, attribute, holder) {
    let lock = this.held.get(el);
    if (!lock) {
      lock = { attribute, original: el.hasAttribute(attribute), holders: new Set() };
      this.held.set(el, lock);
      el.setAttribute(attribute, "");
    }
    lock.holders.add(holder);
  }

  /**
   * `holder` lets go of `el`. The last one to gives it back what it had.
   * @param {E} el
   * @param {string} holder
   * @returns {boolean} whether the lock went
   */
  unlock(el, holder) {
    const lock = this.held.get(el);
    if (!lock || !lock.holders.delete(holder) || lock.holders.size) return false;
    this.held.delete(el);
    if (lock.original) el.setAttribute(lock.attribute, "");
    else el.removeAttribute(lock.attribute);
    return true;
  }

  /**
   * Whether `holder` holds a lock on `el`.
   * @param {E} el
   * @param {string} holder
   */
  holds(el, holder) {
    return this.held.get(el)?.holders.has(holder) ?? false;
  }

  /**
   * Whether `el` has `attribute` of its own, the lock aside: what a form
   * submission reads as disabled, for one.
   * @param {E} el
   * @param {string} attribute
   */
  own(el, attribute) {
    const lock = this.held.get(el);
    return lock && lock.attribute === attribute ? lock.original : el.hasAttribute(attribute);
  }

  /**
   * A morph is about to copy `to`'s attributes onto the locked `from`: what
   * `to` says is the element's own state from now on, and `to` takes the lock.
   * @param {E} from
   * @param {E} to
   */
  overlay(from, to) {
    const lock = this.held.get(from);
    if (!lock) return;
    lock.original = to.hasAttribute(lock.attribute);
    to.setAttribute(lock.attribute, "");
  }

  /**
   * A navigation's morph pairs `el` with the next page's element: the lock is
   * not that element's, and its attributes are the next page's.
   * @param {E} el
   */
  forget(el) {
    this.held.delete(el);
  }
}
