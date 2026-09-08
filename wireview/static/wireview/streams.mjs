/**
 * Stream DOM decisions (see docs/tutorials/06-streams-api.md).
 *
 * A stream's items are owned by stream operations, not by the component render:
 * the component template only ever renders the empty container. Two rules follow,
 * and both are pure decisions about what to do with a node, so they live here
 * without a DOM and are tested with `node --test tests/js/`.
 */

/** Attribute that marks a stream container. */
export const STREAM_ATTRIBUTE = "wire-stream";

/** DOM Node.ELEMENT_NODE, spelled out so this module needs no DOM. */
const ELEMENT_NODE = 1;

/**
 * Is this node a stream container whose children a render must not touch?
 *
 * A re-render carries the template's empty container. Morphing it over the live
 * one would delete every streamed item, so the morph skips these nodes.
 *
 * @param {{nodeType?: number, hasAttribute?: (name: string) => boolean}} node
 * @returns {boolean}
 */
export function isStreamContainer(node) {
  if (!node || node.nodeType !== ELEMENT_NODE) return false;
  return typeof node.hasAttribute === "function" && node.hasAttribute(STREAM_ATTRIBUTE);
}

/**
 * Where an incoming stream item goes.
 *
 * An id already in the container means the server is sending a newer version of
 * an item the user is looking at, so it is replaced where it stands rather than
 * added a second time.
 *
 * @param {{exists: boolean, at: number, childCount: number}} state
 * @returns {{mode: "replace"} | {mode: "prepend"} | {mode: "append"} | {mode: "before", index: number}}
 */
export function planInsert({ exists, at, childCount }) {
  if (exists) return { mode: "replace" };
  if (at === 0) return { mode: "prepend" };
  if (at === -1 || at >= childCount) return { mode: "append" };
  return { mode: "before", index: at };
}

/**
 * How many items to drop, and from which end, once a limit is exceeded.
 * New items arrive at the end that was inserted into, so the other end is trimmed.
 *
 * @param {{childCount: number, limit: number, at: number}} state
 * @returns {{count: number, fromEnd: boolean}}
 */
export function planTrim({ childCount, limit, at }) {
  if (!limit || limit <= 0 || childCount <= limit) return { count: 0, fromEnd: false };
  return { count: childCount - limit, fromEnd: at === 0 };
}
