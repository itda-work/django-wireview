/**
 * Which upload manager a component's files belong to, and when it ends (#137).
 *
 * A manager holds what the browser keeps for one server instance of a
 * component: the configs, the entries and their requests, the preview blob
 * URLs, and the files chosen before the config arrived. It ends with that
 * instance -- the component leaves the page, its join fails, a new join
 * replaces it under the same id (a boosted navigation, a crash's rejoin), or
 * the connection it was joined on closes. Kept past that, a file chosen for the
 * old instance was registered with whatever came next under the id: a late
 * config for the component that had left, or the new instance's.
 *
 * Which config is whose. The server drops an upload op sent by an instance that
 * is no longer the one under its id (WireviewSession.component_upload_op), so
 * nothing the old instance sends after the server handled the leave or the new
 * join reaches the page. What it sent before still can, after the page sent the
 * leave or the join: the two travel in opposite directions. So a config for an
 * id is taken only while the page has no join for it unanswered and the id has
 * not left since its last join. The server answers joins in the order they came,
 * one answer each, and everything it sends after an answer comes from the
 * instance that answer made. A render proves nothing: the old instance renders
 * too, and its renders may still be on their way.
 *
 * Files chosen while no instance is live -- before the first join is answered,
 * or while the socket is down -- are no instance's yet. They wait in a manager
 * that no connection owns, and the next instance's config takes them.
 *
 * Pure bookkeeping; wireview.js builds the managers and does the DOM work.
 */

/**
 * @typedef {{ dispose(): void }} Disposable
 */

/**
 * Whether a render answers a join. The answer carries the server's protocol
 * version (`vsn`); a server older than vsn 3 never sends it, and on such a
 * connection -- the page has heard no version (`serverVsn` 0) -- any render may
 * be the answer, as it was before #137.
 * @param {unknown} vsn - the render's `vsn`
 * @param {number} serverVsn - what the connection has heard so far
 * @returns {boolean}
 */
export function answersJoin(vsn, serverVsn) {
  return typeof vsn === "number" || serverVsn === 0;
}

/** @template {Disposable} M */
export class UploadManagers {
  /**
   * @param {(id: string) => M} create - builds the manager for a component id
   */
  constructor(create) {
    this.create = create;
    /** @type {Map<string, M>} */
    this.byId = new Map();
    /**
     * Ids that left the page or failed to join, and have not joined since.
     * @type {Set<string>}
     */
    this.ended = new Set();
    /**
     * The joins sent on this connection and not yet answered, by the id that
     * joined, oldest first. Each lists the ids its answer settles: the
     * component and the LiveComponents inside it, whose instances its parent's
     * join makes.
     * @type {Map<string, string[][]>}
     */
    this.unanswered = new Map();
    /**
     * Ids whose manager took a config on this connection: it belongs to an
     * instance the connection holds, and ends when the connection does.
     * @type {Set<string>}
     */
    this.owned = new Set();
  }

  /**
   * The manager for a component, made on first use: a file chosen before the
   * component has a manager starts one.
   * @param {string} id
   * @returns {M}
   */
  get(id) {
    let manager = this.byId.get(id);
    if (!manager) {
      manager = this.create(id);
      this.byId.set(id, manager);
    }
    return manager;
  }

  /**
   * The manager for a component if it has one; never makes one.
   * @param {string} id
   * @returns {M | undefined}
   */
  find(id) {
    return this.byId.get(id);
  }

  /**
   * Whether a config for `id` now would come from an instance the page has
   * already given up on.
   * @param {string} id
   * @returns {boolean}
   */
  closed(id) {
    if (this.ended.has(id)) return true;
    for (const joins of this.unanswered.values()) {
      if (joins.some((ids) => ids.includes(id))) return true;
    }
    return false;
  }

  /**
   * The manager a config for `id` goes to, or null when the config comes from
   * an instance that has ended or is about to be replaced.
   * @param {string} id
   * @returns {M | null}
   */
  forConfig(id) {
    if (this.closed(id)) return null;
    this.owned.add(id);
    return this.get(id);
  }

  /**
   * The page is sending a join for `id`. `replacing` when an instance this
   * connection already joined is being replaced under the same id: its manager
   * ends, with the ones of the LiveComponents inside it (`nested`), as the
   * server retires them. Until the join is answered, configs for these ids are
   * the old instances'.
   * @param {string} id
   * @param {string[]} [nested]
   * @param {boolean} [replacing]
   */
  joining(id, nested = [], replacing = false) {
    const ids = [id, ...nested];
    if (replacing) ids.forEach((each) => this.release(each));
    ids.forEach((each) => this.ended.delete(each));
    const joins = this.unanswered.get(id) ?? [];
    joins.push(ids);
    this.unanswered.set(id, joins);
  }

  /**
   * The server answered the oldest join for `id` still unanswered: a render
   * that says so (it carries the server's `vsn`), an `error` during the join,
   * or a `remove` of a join refused. An answer to nothing is ignored.
   * @param {string} id
   */
  answered(id) {
    const joins = this.unanswered.get(id);
    if (!joins) return;
    joins.shift();
    if (!joins.length) this.unanswered.delete(id);
  }

  /**
   * Ends a component's manager: its instance left, or its join failed. Configs
   * still on their way for it are dropped until the page joins the id again,
   * and the next manager starts empty.
   * @param {string} id
   */
  dispose(id) {
    this.ended.add(id);
    this.release(id);
  }

  /**
   * The connection closed. The managers of the instances it held end, once;
   * the files chosen for no instance yet stay for the next connection's.
   * Nothing more arrives from the old socket, and the joins it was owed
   * answers for will not be answered.
   */
  connectionClosed() {
    for (const id of [...this.owned]) this.release(id);
    this.ended.clear();
    this.unanswered.clear();
  }

  /**
   * Disposes the manager under `id`, if any.
   * @param {string} id
   * @private
   */
  release(id) {
    this.owned.delete(id);
    const manager = this.byId.get(id);
    if (!manager) return;
    this.byId.delete(id);
    manager.dispose();
  }
}
