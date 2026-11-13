// A task's run control: Run now, Nudge or Copy brief, from its agents' `wake`, and the
// pickup state shown on the card and in the task window.

import type { RunMode, Item } from '../types';
import { ApiError, get, post } from '../api';
import { agents } from '../agents.svelte';
import { pickupSince, pickupStatus } from '../pickup';
import { isClosed } from '../views';
import { BoardItems } from './items.svelte';

export class BoardRun extends BoardItems {
  /** Can Run now be offered: an agent is assigned and the task is open. */
  canRun(item: Item): boolean {
    return !isClosed(item) && !item.archived_at && item.assignees.some((a) => a.kind === 'agent');
  }

  /** What the task's run control does, from its agents' `wake`: `run` when any agent has a
   * runner (push), `nudge` when they only poll from their own product (pull: its owner is told),
   * `brief` when a person carries the task to the agent's chat (manual: Copy brief). */
  runMode(item: Item): RunMode | null {
    if (!this.canRun(item)) return null;
    const wakes = item.assignees
      .filter((a) => a.kind === 'agent')
      .map((a) => this.accounts[a.id]?.wake ?? 'push');
    if (wakes.includes('push')) return 'run';
    return wakes.includes('pull') ? 'nudge' : 'brief';
  }

  /** The run control's words: Run now, Nudge or Copy brief. */
  runLabel(item: Item): string {
    const mode = this.runMode(item);
    return mode === 'nudge' ? 'Nudge' : mode === 'brief' ? 'Copy brief' : 'Run now';
  }

  /** Shared by the card and task window; derives from live board reports, not runner capacity. */
  pickup(item: Item) {
    if (this.runMode(item) !== 'run') return null;
    // A press this page saw, else the item's own stamp (so a wait survives a reload).
    const stamp = item.run_requested_at ? Date.parse(item.run_requested_at) : undefined;
    const since = pickupSince(item, this.runRequested[item.id] ?? stamp, this.now);
    if (since == null) return null;
    const ids = item.assignees
      .filter((a) => a.kind === 'agent' && (this.accounts[a.id]?.wake ?? 'push') === 'push')
      .map((a) => a.id);
    return pickupStatus(item, since, ids, this.workingByAgent, agents.roster, agents.rosterAt, this.now);
  }

  /** The names of the task's agents that no runner wakes (for the Nudge and brief text). */
  pulledAgents(item: Item): string {
    const names = item.assignees
      .filter((a) => a.kind === 'agent' && (this.accounts[a.id]?.wake ?? 'push') !== 'push')
      .map((a) => a.display_name);
    return names.join(' and ') || 'the agent';
  }

  /** The run control's action: Run now and Nudge record a run request; Copy brief copies the
   * task as a paste-ready prompt (GET /items/{id}/brief). */
  async runAction(item: Item) {
    const mode = this.runMode(item);
    if (mode === 'brief') return this.copyBrief(item);
    if (mode) return this.runNow(item.id);
  }

  async copyBrief(item: Item) {
    let text: string;
    try {
      text = (await get<{ text: string }>(`/items/${item.id}/brief`)).text;
    } catch (e) {
      this.flash(e instanceof ApiError ? e.message : 'Could not build the brief');
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      this.flash(
        `Brief copied. Paste it into ${this.pulledAgents(item)}, then paste the answer back here as a comment.`,
      );
    } catch {
      this.flash(text);
    }
  }

  /** Ask the task's agents to start on it now (POST /items/{id}/run). */
  async runNow(id: string) {
    this.runRequested[id] = Date.now();
    try {
      await post(`/items/${id}/run`);
    } catch (e) {
      delete this.runRequested[id];
      this.flash(e instanceof ApiError ? e.message : 'Could not ask the agent to run it');
      return;
    }
    void agents.refreshRoster();
  }
}
