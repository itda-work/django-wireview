/**
 * Which elements show a loading state, and which answer ends it (#118).
 *
 * An element that sends an event is marked loading (`wireview-loading`, the
 * event's class, `wire-disabled-with`) until the event is answered. Every
 * render of the component used to end it, so a render that answered something
 * else -- a background task's, the join's arriving after the click -- brought
 * a button back enabled mid-save.
 *
 * Now the mark waits for its own answer: the render or `error` that carries
 * the event's `ref`. The server answers one socket's events in order, so the
 * answer to a ref also settles every earlier one. An event sent without a ref
 * -- the server does not echo them, or had not said yet whether it does --
 * gets `null`, and the next render of its component other than the join's
 * ends it, as before.
 *
 * Pure bookkeeping; wireview.js applies and removes the marks on the DOM.
 */

/**
 * @template E
 * @typedef {{ ref: number | null, componentId: string, elements: E[], eventType: string | undefined }} Mark
 */

/** @template E */
export class LoadingLedger {
  constructor() {
    /** @type {Mark<E>[]} */
    this.marks = [];
  }

  /**
   * @param {number | null} ref - the event's ref, or null when the server does not echo refs
   * @param {string} componentId
   * @param {E[]} elements
   * @param {string} [eventType]
   */
  track(ref, componentId, elements, eventType) {
    if (elements.length) this.marks.push({ ref, componentId, elements, eventType });
  }

  /**
   * The answer to `ref` arrived: it and every earlier ref are settled.
   * @param {number} ref
   * @returns {E[]} the elements no remaining mark holds
   */
  answer(ref) {
    return this.#settle((mark) => mark.ref !== null && mark.ref <= ref);
  }

  /**
   * A render of `componentId` from a server that does not echo refs: it
   * answers whatever that component was waiting for.
   * @param {string} componentId
   * @returns {E[]}
   */
  answerUnpaired(componentId) {
    return this.#settle((mark) => mark.ref === null && mark.componentId === componentId);
  }

  /**
   * The component was discarded and joins again (`error`): nothing it was
   * waiting for will be answered.
   * @param {string} componentId
   * @returns {E[]}
   */
  abandon(componentId) {
    return this.#settle((mark) => mark.componentId === componentId);
  }

  /**
   * The connection closed: no answer to anything sent on it will come.
   * @returns {E[]}
   */
  clear() {
    return this.#settle(() => true);
  }

  /**
   * The marks still waiting, for putting back what a morph took away.
   * @returns {Mark<E>[]}
   */
  pending() {
    return this.marks;
  }

  /**
   * @param {(mark: Mark<E>) => boolean} settled
   * @returns {E[]}
   */
  #settle(settled) {
    const done = this.marks.filter(settled);
    if (!done.length) return [];
    this.marks = this.marks.filter((mark) => !settled(mark));
    const held = new Set(this.marks.flatMap((mark) => mark.elements));
    return [...new Set(done.flatMap((mark) => mark.elements))].filter((el) => !held.has(el));
  }
}
