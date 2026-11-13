// The board's single in-memory store. One instance (`board`), exported as an object whose
// fields are $state, so components read `board.items[id]` reactively (Svelte: exported state
// must not be reassigned, only its properties).
//
// The store is one class built in layers, one module each under lib/board/, each layer
// extending the one before it (docs/board.md, "The store"):
//
//   state      the $state fields, the plain mirror of the items, derived reads, the toast
//   prefs      board preferences on the account, and this device's quick-add list and AI add
//   views      saved views and their drafts, the URL for a scope and view
//   load       the REST load and the caches (tags, rules, favorites...), the open task's detail
//   stream     sign in and out, start and retry, the live stream and how each event applies
//   items      item writes: optimistic updates and rollbacks, undo, drops, bulk
//   run        a task's run control (Run now, Nudge, Copy brief) and its pickup state
//   editors    sections, lists, statuses, rules, tags, capacity, the favorites bar
//   selection  the multi-select set and the dialogs the board opens

import { BoardSelection } from './board/selection.svelte';

export { scopeKey, type ActiveView, type Conn, type Selection } from './board/state.svelte';

class Board extends BoardSelection {}

export const board = new Board();
