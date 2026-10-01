/**
 * Commands aimed at a component's element -- a stream op, a JS command, an
 * event for its hooks -- that can arrive before the element does.
 *
 * A render patches the DOM on the next animation frame, but what a new
 * component sends from `joined()` arrives right behind that render, before the
 * frame: its element, and what is inside it, are not there yet. So a command
 * that finds no target waits for the next frame, which runs after the morphs
 * already scheduled (frame callbacks run in the order they were asked for).
 *
 * The target can also be there and not yet be what the command is for: a
 * handler whose render reveals an element, and that sends a JS command for it
 * or an event for a hook on it, sends them right behind that render too. So
 * an owner with a patch on its way (`patching`) holds its commands for the
 * frame as well, though its element is on the page.
 *
 * Order is kept per component, the command's owner: every command of an owner
 * behind a held one waits with it, while another owner's commands apply as they
 * come. Two components' commands touch different elements, so holding them
 * back would keep no order worth keeping -- and in a background tab, where no
 * frame runs, it piled up every component's stream until the tab came back.
 * Commands without an owner (an older server's stream ops) share one key.
 *
 * One frame and no more: a command that still finds nothing then is for a
 * component that left, or never came, and is dropped. One that throws is
 * reported and the rest still apply.
 */

/**
 * @typedef {object} Targeted
 * @property {() => any} find - the command's target, or null while it is not on the page
 * @property {(target: any) => void} apply
 * @property {() => void} [drop] - hears that the target never came
 */

export class TargetQueue {
  /**
   * @param {{
   *   schedule: (callback: () => void) => void,
   *   report?: (error: unknown) => void,
   *   patching?: (owner: string | undefined) => boolean,
   * }} hooks - `schedule` runs a callback on the next frame; `report` hears of
   *   a command that threw while the held ones were applied; `patching` says
   *   whether a patch of the owner's element waits for the next frame
   */
  constructor({ schedule, report = () => {}, patching = () => false }) {
    this.schedule = schedule;
    this.report = report;
    this.patching = patching;
    /** @type {Map<string | undefined, Targeted[]>} the held commands of each owner, in arrival order */
    this.held = new Map();
  }

  /**
   * @param {string | undefined} owner - the component the command is for
   * @param {Targeted} command
   */
  push(owner, command) {
    const held = this.held.get(owner);
    if (held) {
      held.push(command);
      return;
    }
    const target = this.patching(owner) ? null : command.find();
    if (target) {
      command.apply(target);
      return;
    }
    this.held.set(owner, [command]);
    this.schedule(() => this.flush(owner));
  }

  /**
   * Applies an owner's held commands, now that the frame's morphs have run.
   * Not asked whether the owner is patching again: a patch scheduled since is
   * a later render's, and the commands were for what the earlier one drew.
   * @param {string | undefined} owner
   */
  flush(owner) {
    const held = this.held.get(owner) ?? [];
    this.held.delete(owner);
    for (const command of held) {
      // One that throws must not take the ones behind it along
      try {
        const target = command.find();
        if (target) command.apply(target);
        else command.drop?.();
      } catch (error) {
        this.report(error);
      }
    }
  }
}
