/**
 * When the page joins its components again after a template changed (#180).
 *
 * The server sends `rejoin` once it has answered the message it was handling,
 * but the page may have sent more since: an event whose render is still on
 * its way, a join not yet answered. Joining again before that render lands
 * reads a state the event has not reached, and the join puts back what the
 * event did -- its render, answering the instance the join replaces, is
 * dropped (`Joins.render`).
 *
 * So the page asks `sync` and waits. The server handles one socket's messages
 * in order and answers `synced` behind the answers to everything sent before
 * it, events without a ref included. Until then whatever the page would send
 * waits too, so nothing reaches the instances about to be replaced.
 *
 * The answer opens the joins, and holding goes on through them: landing a
 * render still waiting for its frame runs the page's DOM callbacks and hooks,
 * and what they send is for the new instances too. Only the joins themselves
 * go out (the caller sends them past the barrier); `release()` then hands
 * back everything held, in the order it was sent.
 *
 * A second `rejoin` while waiting is the same one: the joins read the states
 * when the answer comes. An answer that never comes does not hold the page
 * for good: after `SYNC_TIMEOUT_MS` it gives up the joins and lets go of what
 * it held -- the page is as it was before the save. A closed socket ends the
 * wait and what it held: the reconnect joins everything again, as it does
 * for what a closed socket drops.
 *
 * Pure bookkeeping; wireview.js sends the messages and does the joins.
 */

/** How long a rejoin waits for its `synced` before it gives up. */
export const SYNC_TIMEOUT_MS = 10000;

/** @typedef {{command: string, payload: any}} Held */

export class RejoinBarrier {
  constructor() {
    /** @type {"idle" | "waiting" | "joining"} */
    this.phase = "idle";
    /** @type {number | null} the ref of the `sync` waited for */
    this.waiting = null;
    /** @type {Held[]} what the page sent meanwhile */
    this.held = [];
  }

  /**
   * A `rejoin` came.
   * @param {() => number} nextRef - draws the connection's next ref
   * @returns {number | null} the ref of the `sync` to send, or null when one is already waited for
   */
  asked(nextRef) {
    if (this.phase !== "idle") return null;
    this.phase = "waiting";
    this.waiting = nextRef();
    return this.waiting;
  }

  /**
   * Whether a message the page is about to send waits: all but the `sync`
   * itself, until `release()`. The joins a rejoin sends are let past by the
   * caller, not here.
   * @param {string} command
   * @returns {boolean}
   */
  holds(command) {
    return this.phase !== "idle" && command !== "sync";
  }

  /** @param {Held} message */
  hold(message) {
    this.held.push(message);
  }

  /**
   * A `synced` came: whether it opens the joins. Holding goes on until `release()`.
   * @param {unknown} ref
   * @returns {boolean} false when it answers no `sync` waited for
   */
  synced(ref) {
    if (this.phase !== "waiting" || ref !== this.waiting) return false;
    this.phase = "joining";
    this.waiting = null;
    return true;
  }

  /**
   * The refs of the joins held: components a navigation brought while the
   * rejoin waited, joining on their own with the state they arrived with.
   * The rejoin leaves them to those joins, which go after the leaves and the
   * `navigated` held before them, as a navigation's must (#146, #170).
   * @returns {Set<number>}
   */
  heldJoins() {
    return new Set(
      this.held
        .filter(({ command, payload }) => command === "join" && typeof payload?.ref === "number")
        .map(({ payload }) => payload.ref)
    );
  }

  /**
   * The joins went out (or are given up): what was held, to send now, in order.
   * @returns {Held[]}
   */
  release() {
    const held = this.held;
    this.phase = "idle";
    this.waiting = null;
    this.held = [];
    return held;
  }

  /**
   * The wait for `ref` timed out.
   * @param {number} ref
   * @returns {Held[] | null} what was held, to send without the joins; null when that wait is over
   */
  expired(ref) {
    if (this.phase !== "waiting" || ref !== this.waiting) return null;
    return this.release();
  }

  /** The connection closed: no answer will come, and what was held has nowhere to go. */
  clear() {
    this.phase = "idle";
    this.waiting = null;
    this.held = [];
  }
}
