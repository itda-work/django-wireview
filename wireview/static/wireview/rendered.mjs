/**
 * Client side of the Phoenix-style diff (see docs/features/html-diff.md).
 *
 * A render is statics interleaved with dynamics. A dynamic is a string, a
 * comprehension `{s: [...], d: [[...], ...]}` (the item template's statics
 * once and one list of dynamics per item), a block `{r: [...], d: [...]}`
 * (an `{% if %}` branch: a nested render with its own statics), or a
 * component reference `{c: id}` (a nested LiveComponent: its HTML lives in
 * that component's own render and is substituted through the `resolve`
 * callback when the parent is built). Partial diffs map a dynamic index to a
 * new string, a full comprehension, an item update `{u: {index: [dynamics]},
 * n: count}`, an item rearrangement `{k: [segment, ...]}` (protocol version
 * 2: a segment is `[start, length]`, a run of the previous items, or
 * `{d: [dynamics]}`, a new item), a full block, a nested block partial
 * `{p: {index: value}}`, or a reference.
 *
 * Pure functions, no DOM: tested with `node --test tests/js/`.
 */

/**
 * @typedef {string | Comprehension | Block | ComponentRef} Dynamic
 * @typedef {{s: string[], d: Dynamic[][]}} Comprehension
 * @typedef {{r: string[], d: Dynamic[]}} Block
 * @typedef {{c: string}} ComponentRef
 * @typedef {{u: Object<string, Dynamic[]>, n: number}} ComprehensionUpdate
 * @typedef {[number, number] | {d: Dynamic[]}} Segment
 * @typedef {{k: Segment[]}} ComprehensionMoves
 * @typedef {{p: Object<string, *>}} BlockPartial
 * @typedef {(id: string) => string} Resolve
 */

/**
 * The diff protocol this client applies, sent as `?vsn=` when the socket
 * opens. The server never sends a newer form. Keep in step with
 * `PROTOCOL_VERSION` in wireview/core/rendered.py.
 */
export const PROTOCOL_VERSION = 2;

/**
 * @param {*} value
 * @returns {value is Comprehension}
 */
export function isComprehension(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    Array.isArray(value.s) &&
    Array.isArray(value.d)
  );
}

/**
 * @param {*} value
 * @returns {value is Block}
 */
export function isBlock(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    Array.isArray(value.r) &&
    Array.isArray(value.d)
  );
}

/**
 * @param {*} value
 * @returns {value is ComponentRef}
 */
export function isComponentRef(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    typeof value.c === "string"
  );
}

/**
 * Interleave statics and dynamics into HTML.
 * @param {string[]} statics
 * @param {Dynamic[]} dynamics
 * @param {Resolve} [resolve] - HTML for a component reference; without it a
 *   reference renders as nothing
 * @returns {string}
 */
export function buildHtml(statics, dynamics, resolve) {
  const parts = [];
  for (let i = 0; i < statics.length; i++) {
    parts.push(statics[i]);
    if (i < dynamics.length) {
      parts.push(renderDynamic(dynamics[i], resolve));
    }
  }
  return parts.join("");
}

/**
 * @param {Dynamic | undefined | null} value
 * @param {Resolve} [resolve]
 * @returns {string}
 */
function renderDynamic(value, resolve) {
  if (value === undefined || value === null) return "";
  if (isComponentRef(value)) {
    return resolve ? resolve(value.c) : "";
  }
  if (isComprehension(value)) {
    return value.d.map((item) => buildHtml(value.s, item, resolve)).join("");
  }
  if (isBlock(value)) {
    return buildHtml(value.r, value.d, resolve);
  }
  return String(value);
}

/**
 * Apply a partial diff in place.
 * @param {Dynamic[]} dynamics - current dynamics (mutated)
 * @param {Object<string, *>} diff - index -> new value
 * @returns {Dynamic[]} the same array
 */
export function applyPartial(dynamics, diff) {
  for (const [key, value] of Object.entries(diff)) {
    const index = parseInt(key, 10);
    if (isNaN(index) || index < 0 || index >= dynamics.length) continue;
    if (isComprehensionUpdate(value)) {
      dynamics[index] = updateComprehension(dynamics[index], value);
    } else if (isComprehensionMoves(value)) {
      dynamics[index] = rearrangeComprehension(dynamics[index], value);
    } else if (isBlockPartial(value)) {
      const current = dynamics[index];
      if (isBlock(current)) applyPartial(current.d, value.p);
    } else {
      dynamics[index] = value;
    }
  }
  return dynamics;
}

/**
 * @param {*} value
 * @returns {value is ComprehensionUpdate}
 */
function isComprehensionUpdate(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value.s) &&
    value.u !== undefined &&
    typeof value.n === "number"
  );
}

/**
 * @param {*} value
 * @returns {value is ComprehensionMoves}
 */
function isComprehensionMoves(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    Array.isArray(value.k) &&
    !Array.isArray(value.s)
  );
}

/**
 * @param {*} value
 * @returns {value is BlockPartial}
 */
function isBlockPartial(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    value.p !== null &&
    typeof value.p === "object" &&
    !Array.isArray(value.r) &&
    !Array.isArray(value.s)
  );
}

/**
 * @param {Dynamic} current
 * @param {ComprehensionUpdate} update
 * @returns {Comprehension}
 */
function updateComprehension(current, update) {
  const target = isComprehension(current) ? current : { s: [], d: [] };
  target.d.length = update.n;
  for (const [key, item] of Object.entries(update.u)) {
    const index = parseInt(key, 10);
    if (!isNaN(index) && index >= 0 && index < update.n) {
      target.d[index] = item;
    }
  }
  return target;
}

/**
 * Build the new item list from runs of the current one and new items.
 *
 * Runs only read the current list, which is left untouched until the new one
 * is complete, so a run may point anywhere (a rotation, a reversal). The
 * server never names one position twice, so reusing item objects is safe. A
 * diff that reads outside the current list is refused whole: applying part
 * of it would leave a list no render ever had. So is one with a segment of
 * neither shape.
 * @param {Dynamic} current
 * @param {ComprehensionMoves} moves
 * @returns {Dynamic}
 */
function rearrangeComprehension(current, moves) {
  if (!isComprehension(current)) {
    console.error("wireview: item rearrangement for a slot that holds no list", moves);
    return current;
  }
  const old = current.d;
  const items = [];
  for (const segment of moves.k) {
    if (Array.isArray(segment)) {
      const [start, length] = segment;
      if (
        !Number.isInteger(start) ||
        !Number.isInteger(length) ||
        start < 0 ||
        length < 1 ||
        start + length > old.length
      ) {
        console.error("wireview: item range outside the current list", segment, old.length);
        return current;
      }
      for (let i = start; i < start + length; i++) items.push(old[i]);
    } else if (segment !== null && typeof segment === "object" && Array.isArray(segment.d)) {
      items.push(segment.d);
    } else {
      console.error("wireview: unknown item segment", segment);
      return current;
    }
  }
  current.d = items;
  return current;
}
