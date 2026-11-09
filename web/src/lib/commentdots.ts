// Comment dots: the small dot in a card's bottom-right corner when a comment is new to you since
// you last opened the card (service/item_reads.py, docs/board.md "Comment dots").
//   mention - you were @mentioned or asked, by anyone (person or agent): pulses blue
//   human   - a person commented: solid blue
//   agent   - only agents commented: small gray
// Per account, so they come from GET /me/comment-dots rather than the shared item view.

import type { SlateEvent } from './types';

export type CommentDot = 'agent' | 'human' | 'mention';

export const DOT_TITLE: Record<CommentDot, string> = {
  mention: 'You were mentioned in a new comment',
  human: 'New comment since you last opened this',
  agent: 'New agent comment since you last opened this',
};

// Events after which this account's dots may have changed.
const COMMENT_NEWS = new Set(['item.commented', 'comment.edited', 'item.mentioned', 'comment.assigned']);

/** Whether `ev` means the dots should be reloaded for account `me`. */
export function dotsChangedBy(ev: SlateEvent, me: string | undefined): boolean {
  if (!me) return false;
  // Opening a card on another device (or this one) clears its dot here too.
  if (ev.type === 'item.read') return ev.subject_id === me;
  // Your own comment is never news to you.
  return COMMENT_NEWS.has(ev.type) && ev.actor_id !== me;
}
