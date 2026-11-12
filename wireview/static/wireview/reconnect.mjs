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
 * The page waits out that backoff itself (`backoffDelay`, `Backoff`) rather
 * than leaving it to reconnecting-websocket, whose wait nothing can cut short:
 * its `reconnect()` does nothing while it waits. What cuts it short is the user
 * coming back (#228): a tab shown again, or the network back. Then a socket
 * that is down opens now, and one that looks open is asked whether it still
 * answers (`Revival`) -- a tab frozen on a phone keeps an OPEN socket whose
 * connection died long ago, and the first click on it went nowhere. Nothing
 * else changes: a page nobody is looking at waits the backoff out, so a deploy's
 * crowd of pages spreads as the settings say.
 *
 * Pure functions and bookkeeping, tested in `tests/js/reconnect.test.mjs`.
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

/**
 * How long the page waits before connecting again, as reconnecting-websocket
 * computes it: nothing before the first attempt after an open, then the first
 * delay, growing by the factor each failed attempt, never past the maximum.
 *
 * @param {{minReconnectionDelay: number, maxReconnectionDelay: number, reconnectionDelayGrowFactor: number}} options
 * @param {number} retryCount - the library's count for the attempt about to start
 * @returns {number}
 */
export function backoffDelay(options, retryCount) {
  if (!(retryCount > 0)) return 0;
  const delay = options.minReconnectionDelay * Math.pow(options.reconnectionDelayGrowFactor, retryCount - 1);
  return Math.min(delay, options.maxReconnectionDelay);
}

/**
 * The backoff the page waits before each attempt, one at a time, which the
 * user's return can end early.
 *
 * reconnecting-websocket awaits the URL its constructor got when that is a
 * function returning a promise; the page gives it one that resolves after
 * `backoffDelay` and sets the library's own delays to 0.
 */
export class Backoff {
  /**
   * @param {{minReconnectionDelay: number, maxReconnectionDelay: number, reconnectionDelayGrowFactor: number}} options
   * @param {{set: (callback: () => void, ms: number) => unknown, clear: (handle: unknown) => void}} timers
   */
  constructor(options, timers) {
    this.options = options;
    this.timers = timers;
    /** @type {(() => void) | null} ends the wait under way */
    this.end = null;
  }

  /** Whether an attempt is waiting out its delay. */
  get waiting() {
    return this.end !== null;
  }

  /**
   * Waits the delay for this attempt, then gives the URL.
   * @param {string} url
   * @param {number} retryCount
   * @returns {Promise<string>}
   */
  wait(url, retryCount) {
    const delay = backoffDelay(this.options, retryCount);
    if (delay <= 0) return Promise.resolve(url);
    return new Promise((resolve) => {
      const done = () => {
        this.timers.clear(handle);
        if (this.end === done) this.end = null;
        resolve(url);
      };
      const handle = this.timers.set(done, delay);
      this.end = done;
    });
  }

  /**
   * Connects now instead of at the end of the wait.
   * @returns {boolean} false when nothing was waiting
   */
  skip() {
    const end = this.end;
    if (end === null) return false;
    end();
    return true;
  }
}

/** The readyState of an open WebSocket. */
const OPEN = 1;

/**
 * How long a socket that looks open has to answer before the page takes it for
 * dead and opens another. The same bar reconnecting-websocket sets a socket
 * that is opening (its `connectionTimeout`): a network that cannot carry a
 * handshake in 4 seconds is not one the page waits on either.
 */
export const PROBE_TIMEOUT_MS = 4000;

/**
 * A socket that heard from the server this recently is alive; asking it again
 * would only race its own traffic. A socket opened a moment ago counts as heard.
 */
export const HEARD_RECENTLY_MS = 1000;

/** The first protocol version whose server answers `sync` with `synced` (#180). */
export const SYNC_SINCE = 8;

/**
 * What the page does when the user comes back to it (#228).
 *
 * - `connect`: the socket is down and waiting out its backoff; open it now.
 * - `probe`: the socket looks open but has been quiet; send `sync` and open
 *   another if nothing comes back within `PROBE_TIMEOUT_MS`.
 * - `none`: the page is hidden, a socket is already opening or being asked,
 *   the server is one that would not answer, or the conversation is not at
 *   rest.
 *
 * Only a conversation at rest is asked: one where the page has sent nothing
 * since it last heard from the server (`awaiting`). The server handles a
 * connection's messages in order, so while it works on a handler -- one that
 * waits on an outside API for seconds -- the `sync` waits behind it too, and
 * silence says nothing about the socket. Asking then took a live socket for
 * dead and threw that handler's render away. The price: a dead socket the
 * page sent an event on before it froze is not caught on return, as before.
 *
 * @param {{visible: boolean, waiting: boolean, readyState: number | undefined,
 *   probing: boolean, serverVsn: number, quietFor: number, awaiting: boolean}} state
 * @returns {"connect" | "probe" | "none"}
 */
export function onReturn({ visible, waiting, readyState, probing, serverVsn, quietFor, awaiting }) {
  // A tab nobody looks at waits the backoff out, as every page did before
  if (!visible) return "none";
  if (waiting) return "connect";
  // Opening (or closing on the way to opening): the attempt under way decides
  if (readyState !== OPEN) return "none";
  if (probing) return "none";
  // A server before vsn 8 drops `sync`: the probe would close a socket that works
  if (serverVsn < SYNC_SINCE) return "none";
  if (quietFor < HEARD_RECENTLY_MS) return "none";
  // Something sent may still be in the server's hands: silence is not death
  if (awaiting) return "none";
  return "probe";
}

/**
 * Whether an event the window fires is the user coming back (#228): the tab
 * shown (`onReturn` reads whether it is visible), the network back, or the
 * page restored from the back/forward cache -- WebKit closes the socket of a
 * page it keeps there, and may bring the page back (navigation.mjs,
 * `arrivesOnRestore`). A `pageshow` of a fresh load is not one: the page is
 * opening its first socket.
 *
 * @param {{type: string, persisted?: boolean}} event
 * @returns {boolean}
 */
export function isReturn(event) {
  if (event.type === "visibilitychange" || event.type === "online") return true;
  return event.type === "pageshow" && event.persisted === true;
}

/**
 * Whether the reconnect a `renew` asked for (#209) is the page's to start.
 * Only on a socket that is open or opening: one that dropped meanwhile is
 * waiting out its backoff, and the socket that wait opens is the renewal
 * already -- `Renewal.opened()` tells its joins so. Starting it early would
 * take a page out of a deploy's spread for no gain.
 *
 * @param {{waiting: boolean, readyState: number | undefined}} state
 * @returns {boolean}
 */
export function renewsNow({ waiting, readyState }) {
  if (waiting) return false;
  return readyState === OPEN || readyState === 0;
}

/**
 * Whether the socket that looks open still answers: the probe a return sends,
 * and what came back since.
 */
export class Revival {
  constructor() {
    /** @type {{ref: number, epoch: number} | null} the `sync` waiting for an answer */
    this.probe = null;
    /** @type {number} when the server was last heard from, by the page's clock */
    this.heardAt = -Infinity;
    /** @type {boolean} whether the page has sent something since it last heard from the server */
    this.awaiting = false;
  }

  /** The page wrote a message on the socket -- not a probe's own `sync`. */
  sent() {
    this.awaiting = true;
  }

  /** Whether a probe is waiting for its answer. */
  get probing() {
    return this.probe !== null;
  }

  /**
   * Something came from the server, or a socket opened: the connection works,
   * and a probe waiting is answered -- any message is, not only its `synced`,
   * because the server may still be busy with what was sent before it.
   * @param {number} now
   */
  heard(now) {
    this.heardAt = now;
    this.probe = null;
    this.awaiting = false;
  }

  /**
   * How long the socket has been quiet.
   * @param {number} now
   */
  quietFor(now) {
    return now - this.heardAt;
  }

  /**
   * A probe went out on the socket of `epoch`.
   * @param {number} ref
   * @param {number} epoch
   */
  probed(ref, epoch) {
    this.probe = { ref, epoch };
  }

  /**
   * The probe's time is up: whether to open another socket. Not when it was
   * answered, nor when the socket it asked has since been replaced.
   * @param {number} ref
   * @param {number} epoch - the socket open now
   * @returns {boolean}
   */
  expired(ref, epoch) {
    const probe = this.probe;
    if (probe === null || probe.ref !== ref) return false;
    this.probe = null;
    return probe.epoch === epoch;
  }

  /** The socket closed: no answer will come on it. */
  closed() {
    this.probe = null;
    this.awaiting = false;
  }
}
