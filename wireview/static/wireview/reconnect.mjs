/**
 * @fileoverview How long the client waits before opening the socket again (#124).
 *
 * A rolling deploy closes every socket a process holds at once, and every page
 * that loses one joins all its components again on the next. How quickly they
 * come back is the load the remaining processes take, so the backoff is the
 * operator's to set: `WIREVIEW["RECONNECT_*"]`, published by
 * `{% wireview_header %}` in a meta tag and read here.
 *
 * The defaults are reconnecting-websocket's own, the values every page used
 * before they were settable: the first retry after 1 to 5 seconds (the jitter
 * is drawn once per page, which is what spreads a crowd of pages apart), each
 * later one 1.3 times longer, never more than 10 seconds.
 *
 * Pure functions, tested in `tests/js/reconnect.test.mjs`.
 */

/** Name of the meta tag `{% wireview_header %}` renders. */
export const META_NAME = "wireview-reconnect";

/** The settings as the page publishes them, in milliseconds. */
export const DEFAULTS = Object.freeze({
  minDelay: 1000,
  jitter: 4000,
  maxDelay: 10000,
  growFactor: 1.3,
});

/** Which `data-*` attribute of the meta carries which setting. */
const ATTRIBUTES = {
  minDelay: "data-min-delay",
  jitter: "data-jitter",
  maxDelay: "data-max-delay",
  growFactor: "data-grow-factor",
};

/**
 * Reads the reconnect settings a document publishes.
 *
 * A setting that is missing or not a usable number takes its default, one by
 * one: a page cached before the meta existed, or a bad value in settings,
 * still reconnects the way it always did rather than in a tight loop.
 *
 * @param {Document|{querySelector: (s: string) => ({getAttribute: (a: string) => string|null}|null)}} doc
 * @returns {{minDelay: number, jitter: number, maxDelay: number, growFactor: number}}
 */
export function readReconnectSettings(doc) {
  const meta = doc?.querySelector?.(`meta[name="${META_NAME}"]`);
  const settings = { ...DEFAULTS };
  if (!meta) return settings;
  for (const [key, attribute] of Object.entries(ATTRIBUTES)) {
    const raw = meta.getAttribute(attribute);
    const value = raw === null || raw === "" ? NaN : Number(raw);
    // A delay may be 0; a factor below 1 would shrink the wait each retry.
    const floor = key === "growFactor" ? 1 : 0;
    if (Number.isFinite(value) && value >= floor) settings[key] = value;
  }
  return settings;
}

/**
 * The options reconnecting-websocket takes, from the settings.
 *
 * @param {{minDelay: number, jitter: number, maxDelay: number, growFactor: number}} settings
 * @param {() => number} [random] - Uniform in [0, 1). Drawn once, for this page.
 * @returns {{minReconnectionDelay: number, maxReconnectionDelay: number, reconnectionDelayGrowFactor: number}}
 */
export function reconnectOptions(settings, random = Math.random) {
  return {
    minReconnectionDelay: settings.minDelay + random() * settings.jitter,
    maxReconnectionDelay: settings.maxDelay,
    reconnectionDelayGrowFactor: settings.growFactor,
  };
}
