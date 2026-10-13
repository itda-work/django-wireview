/**
 * The answers hooks wait for: a `pushEvent` with a callback, or the Promise one
 * without a callback returns (#222).
 *
 * One ledger for the page, keyed by the ref the event carried (`hook-<n>`,
 * counted per page so two components never share one, #108). Each entry names
 * the component that sent it and the hook that asked, so the page can tell
 * which answers will never come:
 *
 * - the socket closed: nothing sent on it will be answered (`disconnected`);
 * - the hook, or its component, left the page (`destroyed`). A callback whose
 *   hook left while its component stayed is still called, as it was before
 *   Promises: only a Promise is given up on then;
 * - the server reported an error for the component, or for the root it belongs
 *   to (`error`). It discarded that instance, so an event written to the socket
 *   before the error came finds no instance: the page joins again only after
 *   the error, and the server reads its messages in order. One a rejoin still
 *   holds back (rejoins.mjs) goes out after the join, to the new instance, and
 *   waits on (`sent`);
 * - the server took the event for an id it holds no instance under -- a join
 *   that failed, a component its drawer retired -- and said so (`gone`, from a
 *   server of protocol 12 on; an older one says nothing).
 *
 * A Promise is rejected then, with a `WireviewPushError` naming the reason. A
 * callback is never called -- as before: code written for callbacks did not
 * expect to hear of failures, and an awaited Promise that never settles hangs
 * whoever awaits it.
 *
 * A reply that came is no longer waited for (`receive`), though it is handed
 * over only once the render before it is on the page (`deliver`, #223): what
 * happens meanwhile -- an error for a later event, the socket closing -- does
 * not undo an answer the server gave. Only the component leaving the page
 * drops a callback then, as it did before Promises; a Promise still hears it.
 */

/** @typedef {"disconnected" | "destroyed" | "error" | "gone"} Reason */

/** Why an answer will not come. */
export class WireviewPushError extends Error {
  /**
   * @param {Reason} reason
   * @param {string} event - the event the hook pushed
   */
  constructor(reason, event) {
    super(`pushEvent("${event}") got no reply: ${reason}`);
    this.name = "WireviewPushError";
    /** @type {Reason} */
    this.reason = reason;
    /** @type {string} */
    this.event = event;
  }
}

/**
 * @typedef {object} Waiting
 * @property {string} owner - the component the event was sent to
 * @property {string} hook - the hook that asked
 * @property {unknown} [by] - whatever the caller says sent it (the hook manager:
 *   the element under an id can be replaced, and the old one's leaving is not the new one's)
 * @property {string} event
 * @property {boolean} promised - a Promise waits, not a callback
 * @property {boolean} sent - written to the socket, not held back or queued
 * @property {(response: any) => void} answer
 * @property {(error: WireviewPushError) => void} fail
 */

export class Replies {
  constructor() {
    /** @type {number} */
    this.last = 0;
    /** @type {Map<string, Waiting>} */
    this.waiting = new Map();
    /** @type {Map<string, {waiting: Waiting, response: any}>} replies that came, to hand over after their render */
    this.arrived = new Map();
  }

  /**
   * A new ref and what answers it. With a callback, the callback hears the
   * response and nothing hears a failure; without one, a Promise.
   * @param {{ owner: string, hook: string, event: string, by?: unknown, callback?: ((response: any) => void) | null }} asked
   * @returns {{ ref: string, promise?: Promise<any> }}
   */
  ask({ owner, hook, event, by, callback }) {
    const ref = `hook-${++this.last}`;
    if (callback) {
      this.waiting.set(ref, { owner, hook, event, by, promised: false, sent: false, answer: callback, fail: () => {} });
      return { ref };
    }
    /** @type {Promise<any>} */
    const promise = new Promise((resolve, reject) => {
      this.waiting.set(ref, { owner, hook, event, by, promised: true, sent: false, answer: resolve, fail: reject });
    });
    // Handled here, so a pushEvent nobody awaits -- fire and forget, as hooks
    // always could -- does not report an unhandled rejection when the socket
    // drops. Whoever awaits it still hears the rejection.
    promise.catch(() => {});
    return { ref, promise };
  }

  /**
   * The event `ref` names was written to the socket.
   * @param {string} ref
   */
  sent(ref) {
    const waiting = this.waiting.get(ref);
    if (waiting) waiting.sent = true;
  }

  /**
   * The reply to `ref` came: nothing gives up on it from here. False when
   * nobody waits for it (answered, or given up on).
   * @param {string} ref
   * @param {any} response
   * @returns {boolean}
   */
  receive(ref, response) {
    const waiting = this.waiting.get(ref);
    if (!waiting) return false;
    this.waiting.delete(ref);
    this.arrived.set(ref, { waiting, response });
    return true;
  }

  /**
   * Hands the reply to `ref` over, if it came and is still to be. A callback
   * that throws is reported, not thrown: the message loop goes on.
   * @param {string} ref
   * @param {(error: unknown) => void} [report]
   */
  deliver(ref, report = () => {}) {
    const arrived = this.arrived.get(ref);
    if (!arrived) return;
    this.arrived.delete(ref);
    try {
      arrived.waiting.answer(arrived.response);
    } catch (error) {
      report(error);
    }
  }

  /**
   * Gives up on `ref`, if it is still waited for.
   * @param {string} ref
   * @param {Reason} reason
   */
  giveUp(ref, reason) {
    const waiting = this.waiting.get(ref);
    if (waiting) this.fail((each) => each === waiting, reason);
  }

  /**
   * Gives up on the refs still waited for whose entry `which` picks. Of the
   * replies that came, a callback is dropped when its component left
   * (`destroyed`); the rest are handed over as they would have been.
   * @param {(waiting: Waiting) => boolean} which
   * @param {Reason} reason
   */
  fail(which, reason) {
    for (const [ref, waiting] of [...this.waiting]) {
      if (!which(waiting)) continue;
      this.waiting.delete(ref);
      waiting.fail(new WireviewPushError(reason, waiting.event));
    }
    if (reason !== "destroyed") return;
    for (const [ref, { waiting }] of [...this.arrived]) {
      if (!waiting.promised && which(waiting)) this.arrived.delete(ref);
    }
  }
}
