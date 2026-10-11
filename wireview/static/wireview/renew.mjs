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
 * Two things are decided here, without a DOM (`tests/js/renew.test.mjs`):
 *
 * - **Once per socket.** Every join the old socket refused says `renew`; the
 *   first one reconnects, the rest are about a socket already going away.
 * - **The new socket says it was opened for this** (`?renewed=1`). The server
 *   does not tell a renewed socket to renew again: if the identity still
 *   differs there, the page is older than the cookies or they never reach the
 *   socket, and another socket would not change that.
 */

export class Renewal {
  constructor() {
    /** @type {number|null} the socket (by epoch) a renew was asked for, once */
    this._asked = null;
    /** @type {boolean} whether the socket being opened is the renewal */
    this._pending = false;
  }

  /**
   * Whether the socket being opened -- by the reconnect `request` started --
   * is a renewal. Read for its URL.
   * @returns {boolean}
   */
  get pending() {
    return this._pending;
  }

  /**
   * The server said `renew` on the socket numbered `epoch`.
   * @param {number} epoch - which socket the page holds now
   * @returns {boolean} whether to reconnect: only for the first on that socket
   */
  request(epoch) {
    if (this._asked === epoch) return false;
    this._asked = epoch;
    this._pending = true;
    return true;
  }

  /**
   * A socket opened. One opened later -- a dropped connection's reconnect --
   * is an ordinary one.
   * @returns {boolean} whether this one was the renewal
   */
  opened() {
    const renewed = this._pending;
    this._pending = false;
    return renewed;
  }
}
