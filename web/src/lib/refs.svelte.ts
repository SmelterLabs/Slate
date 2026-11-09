// What `[[doc:<id>]]` and `[[list:<id>]]` tokens render as on this board: the page's title and
// icon from the docs index (only pages this account can see are in it) and the list's name.
// Pure rendering is in markdown.ts; this is the one place that reads the live stores for it.
import { docs } from './docs.svelte';
import type { RefResolvers } from './markdown';
import { board } from './store.svelte';

export function liveRefs(): RefResolvers {
  return {
    doc: (id) => {
      const d = docs.byId[id];
      return d ? { title: d.title, icon: d.icon } : undefined;
    },
    list: (id) => board.lists[id]?.name,
  };
}
