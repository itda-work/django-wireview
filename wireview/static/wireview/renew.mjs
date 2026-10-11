/**
 * @fileoverview When the page opens a new socket because the server said `renew` (#209).
 *
 * A boosted navigation keeps the socket, and the page it fetches was rendered
 * for whoever the browser's cookies name now. When another tab logged in or
 * out in between, the server answers the new page's joins with `renew`: the
 * socket's handshake was for someone else. The page then opens a socket under
 * the cookies it holds now -- the same reconnect a dropped socket makes -- and
 * joins every component on it.
 *
 * Three things are decided here, without a DOM (`tests/js/renew.test.mjs`):
 *
 * - **Once per socket.** Every join the old socket refused says `renew`; the
 *   first one reconnects, the rest are about a socket already going away.
 * - **The first round of joins on the new socket says so** (`renewed` on each
 *   join). The server does not tell those to renew again: if the identity
 *   still differs there, the page is older than the cookies or they never
 *   reach the socket, and another socket would not change that. Only that
 *   round: a boosted move later on the same socket may meet another tab's
 *   change and be told to renew again.
 * - **Not in a tight loop.** A renew soon after the last one waits, as a
 *   reconnect does: the first at once, each later one in a row longer, by the
 *   page's `RECONNECT_*` settings (`reconnect.mjs`).
 */

/** How long after a renew the next one still counts as the same streak, in milliseconds. */
export const RENEW_STREAK_MS = 60000;

export class Renewal {
  /**
   * @param {{minDelay: number, maxDelay: number, growFactor: number}} [backoff] -
   *   the reconnect settings (`readReconnectSettings`)
   */
  constructor(backoff = { minDelay: 1000, maxDelay: 10000, growFactor: 1.3 }) {
    this._backoff = backoff;
    /** @type {number|null} the socket (by epoch) a renew was asked for, once */
    this._asked = null;
    /** @type {boolean} whether the socket being opened is the renewal */
    this._pending = false;
    /** @type {number|null} when the last renew was asked for */
    this._last = null;
    /** @type {number} how many renews in a row, each within `RENEW_STREAK_MS` of the last */
    this._streak = 0;
  }

  /**
   * The server said `renew` on the socket numbered `epoch`.
   * @param {number} epoch - which socket the page holds now
   * @param {number} now - epoch milliseconds
   * @returns {number|null} how long to wait before reconnecting, or null not
   *   to: a renew on that socket was asked for already
   */
  request(epoch, now) {
    if (this._asked === epoch) return null;
    this._asked = epoch;
    this._pending = true;
    const inStreak = this._last !== null && now - this._last >= 0 && now - this._last < RENEW_STREAK_MS;
    this._streak = inStreak ? this._streak + 1 : 0;
    this._last = now;
    if (this._streak === 0) return 0;
    const { minDelay, maxDelay, growFactor } = this._backoff;
    return Math.min(minDelay * growFactor ** (this._streak - 1), maxDelay);
  }

  /**
   * A socket opened. One opened later -- a dropped connection's reconnect --
   * is an ordinary one.
   * @returns {boolean} whether this one was the renewal: its first round of
   *   joins says `renewed`
   */
  opened() {
    const renewed = this._pending;
    this._pending = false;
    return renewed;
  }
}
