/**
 * Client side of the Phoenix-style diff (see docs/features/html-diff.md).
 *
 * A render is statics interleaved with dynamics. A dynamic is a string, a
 * comprehension `{s: [...], d: [[...], ...]}` (the item template's statics
 * once and one list of dynamics per item), or a block `{r: [...], d: [...]}`
 * (an `{% if %}` branch: a nested render with its own statics). Partial diffs
 * map a dynamic index to a new string, a full comprehension, an item update
 * `{u: {index: [dynamics]}, n: count}`, a full block, or a nested block
 * partial `{p: {index: value}}`.
 *
 * Pure functions, no DOM: tested with `node --test tests/js/`.
 */

/**
 * @typedef {string | Comprehension | Block} Dynamic
 * @typedef {{s: string[], d: Dynamic[][]}} Comprehension
 * @typedef {{r: string[], d: Dynamic[]}} Block
 * @typedef {{u: Object<string, Dynamic[]>, n: number}} ComprehensionUpdate
 * @typedef {{p: Object<string, *>}} BlockPartial
 */

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
 * Interleave statics and dynamics into HTML.
 * @param {string[]} statics
 * @param {Dynamic[]} dynamics
 * @returns {string}
 */
export function buildHtml(statics, dynamics) {
  const parts = [];
  for (let i = 0; i < statics.length; i++) {
    parts.push(statics[i]);
    if (i < dynamics.length) {
      parts.push(renderDynamic(dynamics[i]));
    }
  }
  return parts.join("");
}

/**
 * @param {Dynamic | undefined | null} value
 * @returns {string}
 */
function renderDynamic(value) {
  if (value === undefined || value === null) return "";
  if (isComprehension(value)) {
    return value.d.map((item) => buildHtml(value.s, item)).join("");
  }
  if (isBlock(value)) {
    return buildHtml(value.r, value.d);
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
