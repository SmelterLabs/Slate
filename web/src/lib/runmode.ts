import type { RunMode } from './types';

/** Tooltips for the run control, by what it does (board.runMode). */
export const RUN_TITLE: Record<RunMode, string> = {
  run: 'Run now: ask the assigned agent to start on this now',
  nudge: "Nudge: this agent works from its own app; tell its owner it has a task waiting",
  brief: 'Copy brief: copy this task as a prompt to paste into the agent’s chat',
};
