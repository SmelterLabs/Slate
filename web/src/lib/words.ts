// One word per mechanism. The card, the task window, My Work,
// Needs you, the Inbox, Everything and the pill all say these and no others:
//   a person has to answer      -> "Waiting on Alex" (the person's first name)
//   another task has to finish  -> "Depends on"
//   an agent stopped, named nobody -> "Agent blocked"
//   an agent says the work is done -> "Ready for review"
// The person field in the task window keeps its label "Waiting on"; the dependency picker says
// "Depends on". What leaving the board does is said once, here, and shown when you press it.

export const DEPENDS_ON = 'Depends on';
export const AGENT_BLOCKED = 'Agent blocked';
export const READY_FOR_REVIEW = 'Ready for review';

/** The first word of a display name; "someone" when the account is unknown. */
export function firstName(display: string | null | undefined): string {
  return display?.trim().split(/\s+/)[0] || 'someone';
}

/** "Waiting on Alex". */
export function waitingOn(display: string | null | undefined): string {
  return `Waiting on ${firstName(display)}`;
}

/** What each way off the board does, in one sentence: the toast after the press and the
 * button's tooltip say the same thing. The task verb is "Put away", so it never shares a name
 * with the Archive page (which keeps its four tabs: Done, Put away, Lists and sections, Trash).
 * The API and MCP keep `archive`: agents' words do not change. */
export const LEAVES = {
  complete: 'Done: on the board until midnight, still searchable.',
  putAway: 'Off the board, history kept; bring it back from Archive.',
  trash: 'Deleted for good in 30 days unless you restore it.',
} as const;

export const PUT_AWAY = 'Put away';
