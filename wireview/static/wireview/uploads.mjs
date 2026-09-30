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
 * Which config is whose. The server numbers each instance, names it in the
 * instance's first render (the join's answer, or the parent's render a
 * LiveComponent joined in) and in every config the instance sends. The page
 * holds, for each id, the instance the last such render named, and takes a
 * config only from that instance. Nothing here counts messages: an instance an
 * id no longer has -- it left, or the page sent a join to replace it -- is
 * forgotten, and its configs, whenever they arrive, find no match. The server
 * drops what an ended instance sends after it handled the end
 * (WireviewSession.component_upload_op); this catches what it sent before.
 *
 * A config without a number comes from a server older than #137, which also
 * does not name instances in renders: it is taken, as it was then.
 *
 * Files chosen while no instance is live -- before the first join is answered,
 * or while the socket is down -- are no instance's yet. They wait in a manager
 * no instance owns, and the next instance's config takes them.
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
     * The instance the page holds under each id, as a render named it.
     * @type {Map<string, number>}
     */
    this.instances = new Map();
    /**
     * Ids whose manager took a config, and the instance it came from
     * (`undefined` from a server that does not number them). The manager
     * belongs to that instance and ends with it.
     * @type {Map<string, number | undefined>}
     */
    this.owners = new Map();
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
   * A render named the instance now under `id`. A manager another instance
   * configured ends: that instance is gone.
   * @param {string} id
   * @param {number} instance
   */
  started(id, instance) {
    const owner = this.owners.get(id);
    if (owner !== undefined && owner !== instance) this.release(id);
    this.instances.set(id, instance);
  }

  /**
   * A render the page applied named these instances, each the first render of
   * its instance: the join's answer, and the LiveComponents joined in it. Which
   * renders the page applies -- not one for a component it let go, not one of
   * an instance a join it sent replaces -- is joins.mjs's to say.
   * @param {Object<string, number>} instances
   */
  named(instances) {
    for (const [id, instance] of Object.entries(instances)) this.started(id, instance);
  }

  /**
   * The page is sending a join for `id`. One that replaces an instance -- an
   * earlier join on this connection made it, or was making it -- ends that
   * instance's uploads and its LiveComponents': a file chosen for the old DOM
   * is not the new instance's. The first join replaces nothing, and the files
   * chosen before it wait for its config.
   * @param {string} id
   * @param {string[]} liveIds - the LiveComponents inside the element
   * @param {boolean} replacing - from Joins.sent
   */
  joining(id, liveIds, replacing) {
    if (!replacing) return;
    for (const each of [id, ...liveIds]) this.dispose(each);
  }

  /**
   * The join for `id` failed: there is no instance, so no uploads -- its own
   * nor its LiveComponents', whose configs may already be on their way.
   * @param {string} id
   * @param {string[]} liveIds - the LiveComponents inside the element
   */
  joinFailed(id, liveIds) {
    for (const each of [id, ...liveIds]) this.dispose(each);
  }

  /**
   * The manager a config for `id` goes to, or null when the config comes from
   * an instance other than the one the page holds under the id.
   * @param {string} id
   * @param {number} [instance] - the config's; absent from an older server
   * @returns {M | null}
   */
  forConfig(id, instance) {
    if (instance !== undefined && this.instances.get(id) !== instance) return null;
    this.owners.set(id, instance);
    return this.get(id);
  }

  /**
   * Ends a component's manager and forgets its instance: it left, its join
   * failed, or the page is sending a join that replaces it. Its configs still
   * on their way are dropped, and the next manager starts empty.
   * @param {string} id
   */
  dispose(id) {
    this.instances.delete(id);
    this.release(id);
  }

  /**
   * The connection closed. The managers of the instances it held end, once;
   * the files chosen for no instance yet stay for the next connection's.
   */
  connectionClosed() {
    for (const id of [...this.owners.keys()]) this.release(id);
    this.instances.clear();
  }

  /**
   * Disposes the manager under `id`, if any.
   * @param {string} id
   * @private
   */
  release(id) {
    this.owners.delete(id);
    const manager = this.byId.get(id);
    if (!manager) return;
    this.byId.delete(id);
    manager.dispose();
  }
}
