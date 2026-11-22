// @vitest-environment happy-dom
// A read that fails is told to the person once, in plain words, unless something else already
// says it: the offline bar (no answer at all), the sign-in screen (401), or the viewer's role
// (403, which the board does not show as an error).
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeEventSource, settle, startLive, tearDown, routes, freshBoard, type Board } from './board/testkit';

let board: Board;

/** Pull to refresh: a full load behind a new stream. */
async function refresh() {
  const done = board.refresh();
  FakeEventSource.last.open();
  await done;
  await settle();
}

beforeEach(async () => {
  board = await freshBoard();
});
afterEach(() => tearDown(board));

describe('a failed board read', () => {
  it('names what could not load and the server reason, without an "Error:" prefix', async () => {
    routes.set('/tags', () => ({ status: 503, body: { detail: 'Tags temporarily unavailable' } }));
    await startLive(board);
    await settle();
    expect(board.toast).toBe('Could not load tags: Tags temporarily unavailable');
  });

  it('stays quiet when the viewer is refused (403)', async () => {
    routes.set('/tags', () => ({ status: 403, body: { detail: 'not for you' } }));
    await startLive(board);
    await settle();
    expect(board.toast).toBeNull();
  });

  it('stays quiet when Slate did not answer: the offline bar is the one signal', async () => {
    routes.set('/tags', () => {
      throw new TypeError('Failed to fetch');
    });
    await startLive(board);
    await settle();
    expect(board.toast).toBeNull();
  });
});

describe('project participants', () => {
  const PROJECT = { id: 'S1', name: 'Project', position: 0, private: true, archived_at: null };
  const AGENT = { id: 'A_my', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' };

  it('keep the agents already known when a reload fails, and say so', async () => {
    routes.set('/sections', () => [PROJECT]);
    routes.set('/sections/S1/project', () => ({ participants: [AGENT] }));
    await startLive(board);
    await settle();
    expect(board.sectionAgents.S1?.map((a) => a.handle)).toEqual(['my-agent']);

    routes.set('/sections/S1/project', () => ({ status: 500, body: { detail: 'database is locked' } }));
    await refresh();
    expect(board.sectionAgents.S1?.map((a) => a.handle)).toEqual(['my-agent']);
    expect(board.toast).toBe('Could not load project participants: database is locked');
  });

  it('are quiet when the project has gone or is no longer shared (404, 403)', async () => {
    routes.set('/sections', () => [PROJECT]);
    routes.set('/sections/S1/project', () => ({ participants: [] }));
    await startLive(board);
    await settle();
    expect(board.toast).toBeNull();
    for (const status of [404, 403]) {
      routes.set('/sections/S1/project', () => ({ status, body: { detail: 'gone' } }));
      await refresh();
      expect(board.toast).toBeNull();
    }
  });
});
