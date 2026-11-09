import { describe, expect, it } from 'vitest';
import { canRevert, revertQuestion } from './revert';

const ev = (type: string, field: string | null = null, actor_id = 'agent', session_ref: string | null = null) => ({
  type,
  field,
  actor_id,
  session_ref,
});

describe('canRevert', () => {
  it('offers field changes, status, assignees and moves by someone else', () => {
    expect(canRevert(ev('item.updated', 'title'), 'alex')).toBe(true);
    expect(canRevert(ev('item.updated', 'tags'), 'alex')).toBe(true);
    expect(canRevert(ev('item.status_changed'), 'alex')).toBe(true);
    expect(canRevert(ev('item.assigned', 'assignees'), 'alex')).toBe(true);
    expect(canRevert(ev('item.moved', 'list_id'), 'alex')).toBe(true);
  });
  it('leaves out my own changes, reverts, and other kinds of event', () => {
    expect(canRevert(ev('item.updated', 'title', 'alex'), 'alex')).toBe(false);
    expect(canRevert(ev('item.updated', 'title'), null)).toBe(false);
    expect(canRevert(ev('item.updated', 'title', 'agent', 'revert:0Ab1'), 'alex')).toBe(false);
    expect(canRevert(ev('item.created'), 'alex')).toBe(false);
    expect(canRevert(ev('item.commented'), 'alex')).toBe(false);
    expect(canRevert(ev('item.updated', 'agent_state'), 'alex')).toBe(false);
    expect(canRevert(ev('item.updated', null), 'alex')).toBe(false);
  });
});

describe('revertQuestion', () => {
  it('names the change in plain words', () => {
    expect(revertQuestion(ev('item.updated', 'title'), 'Agent')).toMatch(/^Put back the change to title Agent made\?/);
    expect(revertQuestion(ev('item.updated', 'body_md'), 'Agent')).toContain('the description');
    expect(revertQuestion(ev('item.status_changed'), 'Agent')).toContain('the status change');
    expect(revertQuestion(ev('item.moved'), 'Agent')).toContain('the move');
    expect(revertQuestion(ev('item.assigned'), 'Agent')).toContain('who is on it');
  });
});
