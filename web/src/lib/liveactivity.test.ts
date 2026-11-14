// @vitest-environment happy-dom
// The live activity box's lines (lib/liveactivity.svelte.ts) and how the stream hands them over:
// an `item.activity` message has no id, so it must not touch the cursor, the reducer or the task.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ev } from './fixtures';
import { FakeEventSource, calls, freshBoard, routes, settle, startLive, tearDown, type Board } from './board/testkit';
import { LIVE_KEEP, mergeLines } from './liveactivity.svelte';
import type { SlateEvent } from './types';

const line = (n: number, text = `line ${n}`) => ({ at: `2026-01-01T00:00:${String(n).padStart(2, '0')}.000Z`, text });

describe('mergeLines', () => {
  it('adds new lines in time order and shows a line once', () => {
    const have = [line(1), line(2)];
    expect(mergeLines(have, [line(2), line(3)])).toEqual([line(1), line(2), line(3)]);
    expect(mergeLines(have, [line(1)])).toBe(have);
    expect(mergeLines([line(3)], [line(1)])).toEqual([line(1), line(3)]);
  });
  it('keeps the newest lines only', () => {
    const many = Array.from({ length: LIVE_KEEP + 5 }, (_, n) => ({ at: new Date(Date.UTC(2026, 0, 1, 0, 0, n)).toISOString(), text: `t${n}` }));
    const kept = mergeLines([], many);
    expect(kept).toHaveLength(LIVE_KEEP);
    expect(kept.at(-1)!.text).toBe(`t${LIVE_KEEP + 4}`);
  });
});

describe('the stream', () => {
  let board: Board;
  beforeEach(async () => {
    board = await freshBoard();
    await startLive(board);
    await settle();
  });
  afterEach(() => tearDown(board));

  it('an id-less item.activity fills the box and leaves the cursor, the reducer and the task alone', async () => {
    const { liveActivity } = await import('./liveactivity.svelte');
    FakeEventSource.last.emit(ev({ field: 'title', after: 'Fixed' }));
    await vi.advanceTimersByTimeAsync(1);
    const cursor = board.lastEventId;
    const heard: string[] = [];
    board.onEvent((e) => heard.push(e.type));
    const asked = calls.length;

    const said = { type: 'item.activity', item_id: 'I1', subject_id: 'I1', actor_id: 'A_agent', at: line(1).at, after: { lines: [line(1, 'terminal: ls')] } };
    FakeEventSource.last.emit(said as unknown as SlateEvent);
    await vi.advanceTimersByTimeAsync(60);
    await settle();

    expect(liveActivity.lines.I1).toEqual([line(1, 'terminal: ls')]);
    expect(board.lastEventId).toBe(cursor);
    expect(heard).toEqual([]);
    expect(calls.length).toBe(asked);

    routes.set('/work/I1/activity', () => ({ by: 'A_agent', lines: [line(1, 'terminal: ls'), line(2, 'read_file: a.md')] }));
    await liveActivity.load('I1');
    expect(liveActivity.lines.I1.map((l) => l.text)).toEqual(['terminal: ls', 'read_file: a.md']);
    liveActivity.clear('I1');
    expect(liveActivity.lines.I1).toBeUndefined();
  });
});
