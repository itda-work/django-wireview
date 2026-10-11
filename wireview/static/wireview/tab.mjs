/**
 * The page load's name on the socket URL (#202).
 *
 * A page makes one when it loads and sends it with every socket it opens,
 * reconnects included, so the server can tell this browser tab from another
 * of the same user even though each socket gets a new connection id. Presence
 * keys a tab's meta by it: the join after a reconnect replaces what the tab
 * said before, even when the server that heard it died without its leave.
 * The server reads it as `socket_tab` (wireview/core/rendered.py) and accepts
 * only this form.
 */

/** What the server accepts: URL-safe base64 characters, 16 to 64 of them. */
export const TAB_PATTERN = /^[A-Za-z0-9_-]{16,64}$/;

/**
 * A fresh name: 16 random bytes in URL-safe base64, 22 characters.
 *
 * @param {(bytes: Uint8Array) => Uint8Array} [fill] - Fills the bytes with
 *   random values; `crypto.getRandomValues`, which needs no secure context.
 * @returns {string}
 */
export function newTab(fill = (bytes) => globalThis.crypto.getRandomValues(bytes)) {
  const bytes = fill(new Uint8Array(16));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * The WebSocket URL: the diff forms this client applies, and its page load.
 *
 * @param {{protocol: string, host: string}} location - The page's location.
 * @param {string} path - The socket endpoint, without a leading slash.
 * @param {number} vsn - `PROTOCOL_VERSION`.
 * @param {string} tab - What `newTab` made for this page load.
 * @returns {string}
 */
export function socketUrl(location, path, vsn, tab) {
  const protocol = location.protocol.replace("http", "ws");
  return `${protocol}//${location.host}/${path}?vsn=${vsn}&tab=${encodeURIComponent(tab)}`;
}
