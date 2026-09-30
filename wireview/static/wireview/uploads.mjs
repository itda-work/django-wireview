/**
 * Which upload manager a component's files belong to, and when it ends (#137).
 *
 * A manager holds what the browser keeps for one server instance of a
 * component: the configs, the entries and their requests, the preview blob
 * URLs, and the files chosen before the config arrived. It ends with that
 * instance -- the component leaves the page, its join fails, the socket closes,
 * or a new join replaces it under the same id (a boosted navigation, a crash's
 * rejoin). Kept past that, a file chosen for the old instance was registered
 * with whatever came next under the id: a late config for the component that
 * had left, or the new instance's.
 *
 * Files chosen while no instance is live -- before the first join is answered,
 * or while the socket is down -- go to the manager the next instance takes.
 *
 * Pure bookkeeping; wireview.js builds the managers and does the DOM work.
 */

/**
 * @typedef {{ dispose(): void }} Disposable
 */

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
     * Ids whose instance ended and have not rendered since.
     * @type {Set<string>}
     */
    this.ended = new Set();
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
   * The manager a config for `id` goes to, or null when the config belongs to
   * an instance that has ended. The server answers one socket in order: a
   * config it forwarded before it handled the leave or the new join arrives
   * before the next render of that id, and the next instance's own config
   * after it.
   * @param {string} id
   * @returns {M | null}
   */
  forConfig(id) {
    return this.ended.has(id) ? null : this.get(id);
  }

  /**
   * A render for `id` arrived: an instance under that id is live again, and
   * the configs that follow are its own.
   * @param {string} id
   */
  rendered(id) {
    this.ended.delete(id);
  }

  /**
   * Ends a component's manager: its instance left, failed to join, or is being
   * replaced by a new join under the same id. Configs still on their way for it
   * are dropped until the id renders again, and the next manager starts empty.
   * @param {string} id
   */
  dispose(id) {
    this.ended.add(id);
    const manager = this.byId.get(id);
    if (!manager) return;
    this.byId.delete(id);
    manager.dispose();
  }

  /**
   * Ends every manager: the connection that owned their instances is gone, and
   * nothing more arrives from it.
   */
  disposeAll() {
    for (const id of [...this.byId.keys()]) this.dispose(id);
    this.ended.clear();
  }
}
