import { describe, expect, it } from 'vitest';
import { applyEvent, type BoardData } from './reducer';
import { LIST, ev, item } from './fixtures';

function data(...items: ReturnType<typeof item>[]): BoardData {
  return { items: Object.fromEntries(items.map((i) => [i.id, i])), lists: { L1: LIST } };
}

function snapshotOf(over: Parameters<typeof item>[0], statusId: string | null) {
  const snap = { ...item(over), status_id: statusId } as Record<string, unknown>;
  delete snap.status;
  delete snap.assignees;
  delete snap.tags;
  delete snap.subtask_count;
  return snap;
}

describe('applyEvent', () => {
  it('patches a scalar field from the payload without a refetch', () => {
    const d = data(item());
    const fx = applyEvent(d, ev({ field: 'title', before: 'Fix gutter', after: 'Fix the gutter' }));
    expect(d.items.I1.title).toBe('Fix the gutter');
    expect(fx.refetch).toEqual([]);
    expect(fx.today).toBe(true);
  });

  it('leaves the due date alone on the scheduler due notices (their after is an object)', () => {
    const due = '2026-10-28T09:00:00.000000Z';
    for (const type of ['item.due_soon', 'item.due', 'item.overdue']) {
      const d = data(item({ due_at: due }));
      const fx = applyEvent(d, ev({ type, field: 'due_at', before: null, after: { due_at: due, lead_hours: 24 } }));
      expect(d.items.I1.due_at).toBe(due);
      expect(fx.refetch).toEqual([]);
    }
  });

  it('refetches, never patches, a scalar named by any other item event', () => {
    const d = data(item({ due_at: '2026-10-28T09:00:00Z' }));
    const fx = applyEvent(d, ev({ type: 'item.reminder', field: 'due_at', after: { at: 'x' } }));
    expect(d.items.I1.due_at).toBe('2026-10-28T09:00:00Z');
    expect(fx.refetch).toEqual(['I1']);
  });

  it('moves status using the list statuses', () => {
    const d = data(item());
    const fx = applyEvent(
      d,
      ev({ type: 'item.status_changed', field: 'status_id', before: 'st_open', after: 'st_ready' }),
    );
    expect(d.items.I1.status?.name).toBe('Ready');
    expect(fx.refetch).toEqual([]);
  });

  it('refetches when the status is unknown', () => {
    const d = data(item());
    const fx = applyEvent(d, ev({ type: 'item.status_changed', field: 'status_id', after: 'zz' }));
    expect(fx.refetch).toEqual(['I1']);
  });

  it('replaces tags from the payload', () => {
    const d = data(item({ tags: ['a'] }));
    applyEvent(d, ev({ field: 'tags', before: ['a'], after: ['a', 'b'] }));
    expect(d.items.I1.tags).toEqual(['a', 'b']);
  });

  it('refetches on assignment (the payload only has handles)', () => {
    const d = data(item());
    const fx = applyEvent(d, ev({ type: 'item.assigned', field: 'assignees', after: ['agent'] }));
    expect(fx.refetch).toEqual(['I1']);
  });

  it('builds a created item from the snapshot', () => {
    const d = data();
    const after = snapshotOf({ id: 'I2', title: 'New' }, 'st_ready');
    const fx = applyEvent(d, ev({ type: 'item.created', item_id: 'I2', after }));
    expect(d.items.I2.title).toBe('New');
    expect(d.items.I2.status?.name).toBe('Ready');
    expect(d.items.I2.tags).toEqual([]);
    expect(d.items.I2.subtask_count).toBe(0);
    expect(fx.refetch).toEqual([]);
  });

  it('a created event for an item already held changes nothing', () => {
    const d = data(item({ tags: ['x'] }));
    applyEvent(d, ev({ type: 'item.created', after: snapshotOf({}, 'st_open') }));
    expect(d.items.I1.tags).toEqual(['x']);
  });

  it('counts a new subtask on its parent', () => {
    const d = data(item());
    const after = snapshotOf({ id: 'C1', parent_id: 'I1' }, 'st_open');
    applyEvent(d, ev({ type: 'item.created', item_id: 'C1', after }));
    expect(d.items.I1.subtask_count).toBe(1);
  });

  it('created in a list it does not know: refetch', () => {
    const d = data();
    const after = snapshotOf({ id: 'I3', list_id: 'L9' }, 'x');
    const fx = applyEvent(d, ev({ type: 'item.created', item_id: 'I3', after }));
    expect(fx.refetch).toEqual(['I3']);
  });

  it('archive sets archived_at; delete removes and uncounts', () => {
    const d = data(item({ subtask_count: 1 }), item({ id: 'C1', parent_id: 'I1' }));
    const at = '2026-10-22T20:00:00Z';
    applyEvent(d, ev({ type: 'item.archived', item_id: 'C1', field: 'archived_at', after: at }));
    expect(d.items.C1.archived_at).toBe(at);
    applyEvent(d, ev({ type: 'item.deleted', item_id: 'C1', before: { parent_id: 'I1' } }));
    expect(d.items.C1).toBeUndefined();
    expect(d.items.I1.subtask_count).toBe(0);
  });

  it('trash hides like archive, restore brings back or refetches, purge removes', () => {
    const d = data(item({ subtask_count: 1 }), item({ id: 'C1', parent_id: 'I1' }));
    const at = '2026-10-22T20:00:00Z';
    applyEvent(d, ev({ type: 'item.trashed', item_id: 'C1', after: { trashed_at: at, archived_at: at } }));
    expect(d.items.C1.trashed_at).toBe(at);
    expect(d.items.C1.archived_at).toBe(at);
    applyEvent(d, ev({ type: 'item.restored', item_id: 'C1', after: { trashed_at: null, archived_at: null } }));
    expect(d.items.C1.archived_at).toBeNull();
    const fx = applyEvent(d, ev({ type: 'item.restored', item_id: 'X9', after: { trashed_at: null } }));
    expect(fx.refetch).toEqual(['X9']);
    applyEvent(d, ev({ type: 'item.purged', item_id: 'C1', before: { parent_id: 'I1' } }));
    expect(d.items.C1).toBeUndefined();
    expect(d.items.I1.subtask_count).toBe(0);
  });

  it('an update to an item it does not hold refetches it', () => {
    const d = data();
    const fx = applyEvent(d, ev({ type: 'item.unarchived', field: 'archived_at', after: null }));
    expect(fx.refetch).toEqual(['I1']);
  });

  it('a split refetches the parent', () => {
    const d = data(item());
    expect(applyEvent(d, ev({ type: 'item.split', after: ['C1', 'C2'] })).refetch).toEqual(['I1']);
  });

  it('structure events ask for the nav; status sets reload everything', () => {
    const d = data(item());
    const fx = applyEvent(d, ev({ type: 'list.updated', item_id: null, subject_id: 'L1' }));
    expect(fx.structure).toBe(true);
    expect(fx.reload).toBe(false);
    expect(applyEvent(d, ev({ type: 'list.statuses_changed', item_id: null })).reload).toBe(true);
  });

  it('restoring a section or list reloads everything (its items were left out)', () => {
    const d = data(item());
    for (const type of ['section.unarchived', 'list.unarchived']) {
      const fx = applyEvent(d, ev({ type, item_id: null }));
      expect(fx.structure && fx.reload).toBe(true);
    }
    expect(applyEvent(d, ev({ type: 'list.archived', item_id: null })).reload).toBe(false);
    expect(applyEvent(d, ev({ type: 'section.created', item_id: null })).reload).toBe(false);
  });

  it('a move to another list sets list_id and refetches', () => {
    const d = data(item());
    const fx = applyEvent(
      d,
      ev({ type: 'item.moved', field: 'list_id', before: 'L1', after: 'L2' }),
    );
    expect(d.items.I1.list_id).toBe('L2');
    expect(fx.refetch).toEqual(['I1']);
  });

  it("a comment refetches the card's comment count instead of counting, so a replay cannot double it", () => {
    const d = data(item({ comment_count: 2 }));
    const comment = ev({ type: 'item.commented', after: { id: 'C1', body_md: 'hi' } });
    const fx = applyEvent(d, comment);
    expect(fx.refetch).toEqual(['I1']);
    expect(fx.today).toBe(false);
    expect(applyEvent(d, comment).refetch).toEqual(['I1']); // the catch-up replay of the same event
    expect(d.items.I1.comment_count).toBe(2); // the refetch brings the count; nothing was guessed
    // A comment on a card the board does not hold asks for nothing.
    expect(applyEvent(d, ev({ type: 'item.commented', item_id: 'I9', after: { id: 'C2' } })).refetch).toEqual([]);
  });

  it('a delete seen twice takes one subtask off the parent, not two', () => {
    const d = data(item({ subtask_count: 2 }), item({ id: 'I2', parent_id: 'I1' }));
    const gone = ev({ type: 'item.deleted', item_id: 'I2', before: { parent_id: 'I1' }, after: null });
    expect(applyEvent(d, gone).refetch).toEqual([]);
    expect(d.items.I2).toBeUndefined();
    expect(d.items.I1.subtask_count).toBe(1);
    // Replayed (the stream caught up over the delete again): the parent's count is the
    // server's to say, never decremented a second time.
    const fx = applyEvent(d, gone);
    expect(d.items.I1.subtask_count).toBe(1);
    expect(fx.refetch).toEqual(['I1']);
    // The parent is not on the board either: nothing to ask for.
    const other = ev({ type: 'item.purged', item_id: 'I3', before: { parent_id: 'I8' }, after: null });
    expect(applyEvent(d, other).refetch).toEqual([]);
    expect(d.items.I1.subtask_count).toBe(1);
  });

  it('created, trashed, blocked and reported events applied twice leave the same board', () => {
    const d = data(item({ subtask_count: 0 }));
    const twice = (e: ReturnType<typeof ev>) => {
      applyEvent(d, e);
      const once = JSON.stringify(d);
      applyEvent(d, e);
      expect(JSON.stringify(d)).toBe(once);
    };
    twice(ev({ type: 'item.created', item_id: 'I2', after: snapshotOf({ id: 'I2', parent_id: 'I1' }, 'st_open') }));
    expect(d.items.I1.subtask_count).toBe(1);
    twice(ev({ type: 'item.trashed', item_id: 'I2', after: { trashed_at: '2026-11-04T00:00:00Z', archived_at: '2026-11-04T00:00:00Z' } }));
    twice(ev({ type: 'item.blocked', after: { by: ['I2'] } }));
    twice(ev({ type: 'item.reported', actor_id: 'AGENT', after: { state: 'working', by: 'AGENT' } }));
  });

  it('watching or a mention added by an edit does not bump the comment count', () => {
    const d = data(item());
    applyEvent(d, ev({ type: 'item.watched', after: 'A1' }));
    applyEvent(d, ev({ type: 'item.mentioned', after: { mentions: ['alex'] } }));
    expect(d.items.I1.comment_count ?? 0).toBe(0);
  });

  it("an agent's report sets the card's badge and done clears it", () => {
    const d = data(item());
    applyEvent(
      d,
      ev({
        type: 'item.reported',
        actor_id: 'AGENT',
        at: '2026-10-23T22:00:00.000000Z',
        after: { state: 'working', note: 'On it', progress: 40, by: 'AGENT', url: 'https://x.test/c/1' },
      }),
    );
    expect(d.items.I1.agent).toEqual({
      state: 'working',
      by: 'AGENT',
      at: '2026-10-23T22:00:00.000000Z',
      note: 'On it',
      progress: 40,
      url: 'https://x.test/c/1',
    });
    applyEvent(d, ev({ type: 'item.reported', after: { state: 'done', by: 'AGENT' } }));
    expect(d.items.I1.agent).toBeNull();
  });

  it('keeps agent_threads current from a report, per agent, and past done', () => {
    const d = data(item());
    const thread = { ref: 'sess-1', url: 'hermes://chat/sess-1?profile=agent' };
    applyEvent(d, ev({ type: 'item.reported', actor_id: 'AGENT', after: { state: 'working', by: 'AGENT', thread } }));
    expect(d.items.I1.agent_threads).toEqual([{ account_id: 'AGENT', ...thread }]);
    // The same agent's changed thread replaces its row; another agent's is added beside it.
    applyEvent(d, ev({ type: 'item.reported', actor_id: 'AGENT', after: { state: 'working', by: 'AGENT', thread: { ref: 'sess-2' } } }));
    applyEvent(d, ev({ type: 'item.reported', actor_id: 'AGENT2', after: { state: 'working', by: 'AGENT2', thread: { ref: 'b-1', url: 'https://x.test/b' } } }));
    expect(d.items.I1.agent_threads).toEqual([
      { account_id: 'AGENT', ref: 'sess-2', url: null },
      { account_id: 'AGENT2', ref: 'b-1', url: 'https://x.test/b' },
    ]);
    // done clears the badge, not the threads.
    applyEvent(d, ev({ type: 'item.reported', actor_id: 'AGENT', after: { state: 'done', by: 'AGENT' } }));
    expect(d.items.I1.agent).toBeNull();
    expect(d.items.I1.agent_threads).toHaveLength(2);
  });

  it('ignores token and account events', () => {
    const d = data(item());
    const fx = applyEvent(d, ev({ type: 'token.created', item_id: null }));
    expect(fx).toEqual({
      refetch: [],
      structure: false,
      reload: false,
      today: false,
      views: false,
      favorites: false,
      capacities: false,
      tags: false,
    });
  });

  it('view events ask for the views and touch no item', () => {
    const d = data(item());
    for (const type of ['view.created', 'view.updated', 'view.archived', 'view.unarchived']) {
      const fx = applyEvent(d, ev({ type, item_id: null, field: 'name', after: 'x' }));
      expect(fx).toEqual({
        refetch: [],
        structure: false,
        reload: false,
        today: false,
        views: true,
        favorites: false,
          capacities: false,
          tags: false,
      });
    }
    expect(d.items.I1.title).toBe('Fix gutter');
  });

  it('favorite events ask for the favorites and touch no item', () => {
    const d = data(item());
    const before = JSON.stringify(d);
    for (const type of ['favorite.added', 'favorite.moved', 'favorite.removed']) {
      const fx = applyEvent(d, ev({ type, item_id: null, field: 'position', after: 1 }));
      expect(fx.favorites).toBe(true);
      expect(fx.refetch).toEqual([]);
    }
    expect(JSON.stringify(d)).toBe(before);
  });

  it('refetches a linked item; a finished blocker unblocks through its own event', () => {
    const d = {
      items: { I1: item({ blocking: ['I2'] }), I2: item({ id: 'I2', blocked_by: ['I1'], blocked: true }) },
      lists: { L1: LIST },
    };
    expect(applyEvent(d, ev({ type: 'item.linked', item_id: 'I2' })).refetch).toEqual(['I2']);
    // The server sends item.unblocked for I2 in the same change: no refetch needed.
    const fx = applyEvent(d, ev({ type: 'item.status_changed', after: 'st_done' }));
    expect(fx.refetch).toEqual([]);
    applyEvent(d, ev({ type: 'item.unblocked', item_id: 'I2', after: { cleared_by: ['I1'], open: true } }));
    expect(d.items.I2.blocked).toBe(false);
  });

  it('tag events ask for a reload of the tags, touching no item', () => {
    const d = { items: { I1: item() }, lists: { L1: LIST } };
    expect(applyEvent(d, ev({ type: 'tag.deleted', item_id: null })).tags).toBe(true);
  });
});
