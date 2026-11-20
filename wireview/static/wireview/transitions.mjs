/**
 * How a JS() transition runs (#239, GAP-043), with the DOM and the clock passed
 * in so a test drives both (`node --test tests/js/`).
 *
 * - `transition` alone, as before: its classes stay on for `time` ms, or for
 *   good (until a render that does not carry them) without a time.
 * - With `start` and `end`, Phoenix's three phases: `start` is put on and
 *   painted, then `transition` (the classes that animate) joins it, then `end`
 *   takes `start`'s place; `time` ms later `transition` and `end` come off. A
 *   time of 0 puts nothing on: the command's own change happens at once.
 */

/** A three-phase transition's time when none is given (Phoenix's), in step with js.DEFAULT_TRANSITION_TIME */
export const DEFAULT_TIME = 200;

/**
 * @typedef {Object} Phases
 * @property {string[]} run - the classes that animate
 * @property {string[]} start
 * @property {string[]} end
 * @property {number} time
 * @property {boolean} three - start and end were given
 */

/**
 * @param {string | undefined} classes
 * @returns {string[]}
 */
function names(classes) {
  return typeof classes === "string" ? classes.split(" ").filter(Boolean) : [];
}

/**
 * What a command's transition asks for, or null when it asks for none.
 * @param {{transition?: string, start?: string, end?: string, time?: number} | null | undefined} config
 * @returns {Phases | null}
 */
export function phasesOf(config) {
  if (!config || !config.transition) return null;
  const three = config.start !== undefined || config.end !== undefined;
  const time = typeof config.time === "number" ? config.time : three ? DEFAULT_TIME : 0;
  return { run: names(config.transition), start: names(config.start), end: names(config.end), time, three };
}

/**
 * @typedef {Object} TransitionOps
 * @property {(classes: string[]) => () => void} hold - put classes on until the returned function
 * @property {(classes: string[]) => void} keep - put classes on for good (a transition without a time)
 * @property {() => Promise<void>} frame - the next animation frame
 * @property {(ms: number) => Promise<void>} sleep
 */

/**
 * Run `phases` through `ops`. Every class it holds comes off by the time it
 * resolves, and if it throws.
 * @param {Phases | null} phases
 * @param {TransitionOps} ops
 * @returns {Promise<void>}
 */
export async function runPhases(phases, ops) {
  if (!phases) return;
  const { run, start, end, time, three } = phases;
  if (!(time > 0)) {
    if (!three) ops.keep(run);
    return;
  }
  if (!three) {
    const release = ops.hold(run);
    try {
      await ops.sleep(time);
    } finally {
      release();
    }
    return;
  }
  const releaseStart = ops.hold(start);
  let releaseRun = () => {};
  let releaseEnd = () => {};
  try {
    await ops.frame();
    releaseRun = ops.hold(run);
    await ops.frame();
    releaseEnd = ops.hold(end);
    releaseStart();
    await ops.sleep(time);
  } finally {
    releaseStart();
    releaseRun();
    releaseEnd();
  }
}
