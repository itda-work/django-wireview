/**
 * What JS() commands changed on an element, so a render keeps it (#219).
 *
 * A render morphs an element into the template's HTML, which knows nothing of
 * the classes, attributes and visibility a JS() command gave it on the page.
 * Phoenix LiveView keeps those "sticky" across patches; so does wireview: each
 * change is recorded against its element, and a morph of that element applies
 * the record to the incoming HTML before it is copied over.
 *
 * - Classes record only the change a command made. Adding a class the element
 *   already has records nothing, and adding one a command removed earlier only
 *   drops that removal: what is left is the difference from the server's HTML,
 *   so a class the server takes away later is not put back by a no-op.
 * - Attributes record the last value a command set or removed. Setting the
 *   whole `class` attribute supersedes the class changes before it, and
 *   `hidden` or `style` the visibility before it.
 * - Visibility (`show`, `hide`, `toggle`) records the last state.
 * - A transition's classes stay for as long as it runs, then go.
 *
 * The record goes with the element, not with a selector: an element the render
 * replaces with a new one starts clean, as in LiveView. A boosted navigation's
 * morph forgets what it pairs, since the page is a new one.
 *
 * A command that waits for a transition finishes on the element as it is then
 * (`handle`): the one that took the record's place, or none once the record
 * is forgotten -- a navigation's next page is not the command's to change.
 *
 * No DOM beyond the element methods used: tested with `node --test tests/js/`.
 */

/**
 * @typedef {object} StickyElement
 * @property {{add(...names: string[]): void, remove(...names: string[]): void, contains(name: string): boolean}} classList
 * @property {(name: string, value: string) => void} setAttribute
 * @property {(name: string) => void} removeAttribute
 * @property {boolean} hidden
 * @property {{display: string}} style
 * @property {string|null} [namespaceURI] - an HTML element's names its attributes in any case
 */

const HTML_NAMESPACE = "http://www.w3.org/1999/xhtml";

/**
 * @typedef {object} StickyRecord
 * @property {StickyElement} el - the element the record belongs to now
 * @property {Map<string, boolean>} classes - true for a class a command added, false for one it removed
 * @property {Map<string, string|null>} attrs - the value a command set, or null for one it removed
 * @property {{hidden: boolean, display: string}|null} visibility
 * @property {Map<string, number>} transient - a running transition's classes, by how many run
 * @property {boolean} gone - forgotten: what still runs for it leaves the element alone
 */

/**
 * @typedef {object} StickyHandle
 * @property {() => StickyElement|null} current - the element the command's target is now, or
 *   null once its record was forgotten
 */

export class StickyOps {
  /** @type {WeakMap<object, StickyRecord>} */
  #records = new WeakMap();

  /**
   * @param {StickyElement} el
   * @returns {StickyRecord}
   */
  #record(el) {
    let record = this.#records.get(el);
    if (!record) {
      record = { el, classes: new Map(), attrs: new Map(), visibility: null, transient: new Map(), gone: false };
      this.#records.set(el, record);
    }
    return record;
  }

  /**
   * Follow `el` through what comes after a wait: a replacement takes its place
   * (`move`), a navigation's morph forgets it (`forget`).
   * @param {StickyElement} el
   * @returns {StickyHandle}
   */
  handle(el) {
    const record = this.#record(el);
    return { current: () => (record.gone ? null : record.el) };
  }

  /**
   * @param {StickyElement} el
   * @param {string[]} names
   */
  addClasses(el, names) {
    const { classes } = this.#record(el);
    for (const name of names) {
      if (classes.get(name) === false) classes.delete(name);
      else if (!el.classList.contains(name)) classes.set(name, true);
      el.classList.add(name);
    }
  }

  /**
   * @param {StickyElement} el
   * @param {string[]} names
   */
  removeClasses(el, names) {
    const { classes } = this.#record(el);
    for (const name of names) {
      if (classes.get(name) === true) classes.delete(name);
      else if (el.classList.contains(name)) classes.set(name, false);
      el.classList.remove(name);
    }
  }

  /**
   * @param {StickyElement} el
   * @param {string[]} names
   */
  toggleClasses(el, names) {
    for (const name of names) {
      if (el.classList.contains(name)) this.removeClasses(el, [name]);
      else this.addClasses(el, [name]);
    }
  }

  /**
   * @param {StickyElement} el
   * @param {string} name
   * @param {string|null} value - null removes the attribute
   */
  setAttribute(el, name, value) {
    const record = this.#record(el);
    const lower = name.toLowerCase();
    // An HTML element's attribute is one whatever the case it is named in; an
    // SVG one is case-sensitive (viewBox) and keeps its spelling.
    const attr = el.namespaceURI === HTML_NAMESPACE ? lower : name;
    if (lower === "class") record.classes.clear();
    if (lower === "hidden" || lower === "style") record.visibility = null;
    // The last command's value is the one replayed, after the others
    record.attrs.delete(attr);
    record.attrs.set(attr, value);
    if (value === null) el.removeAttribute(attr);
    else el.setAttribute(attr, value);
  }

  /**
   * @param {StickyElement} el
   * @param {string} [display] - the CSS display to show it with; none clears an inline one
   */
  show(el, display = "") {
    this.#record(el).visibility = { hidden: false, display };
    el.style.display = display;
    el.hidden = false;
  }

  /** @param {StickyElement} el */
  hide(el) {
    this.#record(el).visibility = { hidden: true, display: "" };
    el.hidden = true;
  }

  /**
   * Put a transition's classes on `el` until the returned function is called.
   * @param {StickyElement} el
   * @param {string[]} names
   * @returns {() => void} takes them off again
   */
  transient(el, names) {
    const record = this.#record(el);
    for (const name of names) {
      record.transient.set(name, (record.transient.get(name) ?? 0) + 1);
      el.classList.add(name);
    }
    let released = false;
    return () => {
      if (released) return;
      released = true;
      if (record.gone) return;
      for (const name of names) {
        const count = (record.transient.get(name) ?? 1) - 1;
        if (count > 0) {
          record.transient.set(name, count);
          continue;
        }
        record.transient.delete(name);
        // A class a command keeps, or that the element had anyway, is not the transition's to take
        if (record.classes.get(name) !== true) record.el.classList.remove(name);
      }
    };
  }

  /**
   * Whether commands changed anything on `el` that a render must keep.
   * @param {object} el
   * @returns {boolean}
   */
  has(el) {
    const record = this.#records.get(el);
    return Boolean(
      record && (record.classes.size || record.attrs.size || record.visibility || record.transient.size)
    );
  }

  /**
   * Apply what commands changed on `from` to `to`: the incoming HTML a morph
   * copies onto `from`, or the element that replaces it.
   * @param {object} from
   * @param {StickyElement} to
   */
  apply(from, to) {
    const record = this.#records.get(from);
    if (!record) return;
    for (const [name, value] of record.attrs) {
      if (value === null) to.removeAttribute(name);
      else to.setAttribute(name, value);
    }
    if (record.visibility) {
      if (record.visibility.hidden) {
        to.hidden = true;
      } else {
        to.style.display = record.visibility.display;
        to.hidden = false;
      }
    }
    for (const [name, added] of record.classes) {
      if (added) to.classList.add(name);
      else to.classList.remove(name);
    }
    for (const name of record.transient.keys()) to.classList.add(name);
  }

  /**
   * `to` replaces `from` on the page and is the same element to the user: it
   * keeps what commands changed, and later commands and transitions' ends
   * act on it.
   * @param {object} from
   * @param {StickyElement} to
   */
  move(from, to) {
    const record = this.#records.get(from);
    if (!record) return;
    this.apply(from, to);
    this.#records.delete(from);
    record.el = to;
    this.#records.set(to, record);
  }

  /**
   * Drop what commands changed on `el`: it now stands for something else.
   * @param {object} el
   */
  forget(el) {
    const record = this.#records.get(el);
    if (!record) return;
    record.gone = true;
    this.#records.delete(el);
  }
}
