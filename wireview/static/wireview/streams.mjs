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

/**
 * @typedef {object} ContainerSpot
 * @property {string} owner - id of the component the container sits in
 * @property {string} name - its `wire-stream` name
 * @property {string} id - its id attribute, "" when it has none
 */

/**
 * The ids that pair each live stream container with its place in a render.
 *
 * A render's HTML carries the template's containers, empty and usually without
 * an id. idiomorph pairs nodes without ids by position, looking ahead past
 * siblings: an element the render adds in front of a container (a new
 * component, another list behind an `{% if %}`) took the container's place, and
 * the container -- every streamed item with it -- was removed. A container
 * whose id is in both the page and the render is one idiomorph keeps and moves
 * instead. So each live container and its counterpart in the render, the first
 * of its name in the same component, get the same id, made from the component
 * and the name. A container that has an id in the render already keeps it (the
 * template's, which the page's has too) and still counts as the first of its
 * name; one the page does not have yet is new and gets none. A live container
 * whose id the render does not give it (an id behind an `{% if %}`) takes the
 * made one: the render's attributes are what the morph leaves on it anyway.
 *
 * @param {ContainerSpot[]} live - the containers on the page, in document order
 * @param {ContainerSpot[]} next - the containers in the render, in document order
 * @returns {{live: Map<number, string>, next: Map<number, string>}} the id to
 *   set, by index, on each side
 */
export function pinContainerIds(live, next) {
  const pins = { live: new Map(), next: new Map() };
  /** @type {Map<string, {index: number, id: string}>} */
  const onPage = new Map();
  live.forEach(({ owner, name, id }, index) => {
    const key = JSON.stringify([owner, name]);
    if (!onPage.has(key)) onPage.set(key, { index, id });
  });
  const taken = new Set();
  next.forEach(({ owner, name, id }, index) => {
    const key = JSON.stringify([owner, name]);
    const match = onPage.get(key);
    if (!match || taken.has(key)) return;
    // The first of its name is the one, whether or not it has an id: a second
    // container of the name must not take the live one's place
    taken.add(key);
    if (id) return;
    const pinned = `wire-stream-${owner}-${name}`;
    if (match.id !== pinned) pins.live.set(match.index, pinned);
    pins.next.set(index, pinned);
  });
  return pins;
}
