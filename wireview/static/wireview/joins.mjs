/**
 * Which answers belong to the join the page holds for a component (#139).
 *
 * A component can be joined more than once on one connection: a boosted
 * navigation brings new DOM under the same id, a crash's rollback joins it
 * again. Each join replaces the instance before it, and the server answers in
 * the order it reads -- so the answers to an earlier join (its render, or an
 * `error` its mount or its `params_changed` raised after the render) can reach
 * the page after it sent the next join. Taken for the new join's, an old
 * `error` marked the new element and dropped the component the new render was
 * for: dead until the next connection. An old render named the replaced
 * instance as current, and a file chosen then went to its config.
 *
 * So the page names each join with a ref, to a server that announced it takes
 * one (JOIN_REFS_SINCE), and the server returns it on the render and the error
 * that answer the join. Until the answer comes, everything else for the id is
 * the replaced instance's. After it, the id is the new instance's: the server
 * retired the old one when it read the join, so nothing of it follows.
 * What this holds back is the render and the `error`, the two that carry the
 * ref; `remove`, `joined`, `stream_op` and `exec_js` still land as they come.
 *
 * A join sent without a ref -- the first on a connection, before any answer
 * said which server this is, or to an older server -- pairs nothing, and every
 * answer is taken as it comes, as before.
 *
 * Pure bookkeeping; wireview.js sends the joins and applies the answers.
 */

/**
 * @typedef {{ ref: number | null, answered: boolean }} Join
 */

/**
 * The user event a `render` or an `error` answers, by its ref -- the one whose
 * loading state and value guard the message settles. Joins and events share
 * one counter, so a join's ref must not be read as an event's: taken as one,
 * a component's join answer would settle every event up to it (the value
 * guard settles by `<=`), and another component's committing event would lose
 * the permission to reset its fields.
 * @param {string} command - "render" or "error"
 * @param {{ ref?: number, vsn?: number, during?: string }} payload
 * @returns {number | undefined} undefined when the message answers no event
 *   by ref: a join's answer (a render carrying vsn, a `during: "join"` error),
 *   or a ref the server did not echo
 */
export function settledEvent(command, payload) {
  const { ref } = payload;
  if (typeof ref !== "number") return undefined;
  if (command === "render") return typeof payload.vsn === "number" ? undefined : ref;
  if (command === "error") return payload.during === "event" ? ref : undefined;
  return undefined;
}

export class Joins {
  constructor() {
    /**
     * The last join the page sent for each id on this connection.
     * @type {Map<string, Join>}
     */
    this.byId = new Map();
  }

  /**
   * A join goes out for `id`.
   * @param {string} id
   * @param {number} [ref] - absent when the server does not take one
   * @returns {boolean} whether it replaces an instance an earlier join on this
   *   connection made (or was making)
   */
  sent(id, ref) {
    const replaces = this.byId.has(id);
    this.byId.set(id, { ref: ref ?? null, answered: false });
    return replaces;
  }

  /**
   * Whether the page applies a render for `id`. A render the page takes also
   * answers the join it carries the ref of.
   * @param {string} id
   * @param {number} [ref] - the render's: the join's, or a user event's
   * @param {boolean} registered - the page holds a component under `id`; a
   *   render for one whose element left the page was on its way when the page
   *   let it go
   * @returns {boolean}
   */
  render(id, ref, registered) {
    if (!registered) return false;
    const join = this.byId.get(id);
    if (!join || join.ref === null || join.answered) return true;
    if (ref !== join.ref) return false;
    join.answered = true;
    return true;
  }

  /**
   * Whether an `error` for `id` is about the instance the page holds.
   * @param {string} id
   * @param {number} [ref] - the join's (`during: "join"`) or the event's
   * @param {string} during - "join" or "event"
   * @returns {boolean}
   */
  error(id, ref, during) {
    const join = this.byId.get(id);
    if (!join || join.ref === null) return true;
    // Only the join the page holds can fail for it, before or after its render
    if (during === "join") return ref === join.ref;
    // An event's: until the join is answered, the instance that raised is the
    // one it replaces
    return join.answered;
  }

  /**
   * The component left the page, or its join failed: no answer for it is
   * expected, and the next join under the id replaces nothing.
   * @param {string} id
   */
  forget(id) {
    this.byId.delete(id);
  }

  /** The connection closed: its joins end with it. */
  clear() {
    this.byId.clear();
  }
}
