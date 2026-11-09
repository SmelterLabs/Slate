import { describe, expect, it } from 'vitest';
import { flatChoices, moveGroups } from './move';
import type { List, Section } from './types';

const sec = (id: string, name: string, position: number, archived = false): Section => ({
  id,
  name,
  slug: id,
  position,
  audience: 'personal',
  created_at: '',
  archived_at: archived ? '2026-01-01T00:00:00Z' : null,
});
const list = (id: string, section_id: string, name: string, position: number, archived = false): List => ({
  id,
  section_id,
  name,
  slug: id,
  position,
  default_status_id: null,
  created_at: '',
  archived_at: archived ? '2026-01-01T00:00:00Z' : null,
  statuses: [],
});

const sections = [sec('w', 'Work', 2), sec('p', 'Personal', 1), sec('old', 'Old', 3, true)];
const lists = [
  list('todo', 'p', 'To Do', 2),
  list('home', 'p', 'Home', 1),
  list('crm', 'w', 'Clients', 1),
  list('gone', 'p', 'Gone', 3, true),
  list('o1', 'old', 'Old list', 1),
];

describe('moveGroups', () => {
  it('groups live lists by section in board order', () => {
    const g = moveGroups(sections, lists);
    expect(g.map((x) => x.section.id)).toEqual(['p', 'w']);
    expect(g[0].lists.map((l) => l.id)).toEqual(['home', 'todo']);
    expect(flatChoices(g).map((l) => l.id)).toEqual(['home', 'todo', 'crm']);
  });

  it('filters by list name, ignoring case', () => {
    const g = moveGroups(sections, lists, 'to d');
    expect(flatChoices(g).map((l) => l.id)).toEqual(['todo']);
  });

  it('a section name keeps all its lists, and words can mix section and list', () => {
    expect(flatChoices(moveGroups(sections, lists, 'work')).map((l) => l.id)).toEqual(['crm']);
    expect(flatChoices(moveGroups(sections, lists, 'personal home')).map((l) => l.id)).toEqual(['home']);
  });

  it('drops sections with nothing left', () => {
    expect(moveGroups(sections, lists, 'nothing like this')).toEqual([]);
  });
});
