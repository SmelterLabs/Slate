<script lang="ts">
  import { onDestroy } from 'svelte';
  import { chatPanel } from '../lib/chatpanel.svelte';
  import { get, patch } from '../lib/api';
  import { board } from '../lib/store.svelte';
  import DocsHub from './docs/DocsHub.svelte';
  import { atLeast, sectionLevel } from '../lib/permissions';
  import { safeWhiteboard } from '../lib/collaboration';
  import { myWork } from '../lib/mywork';
  import type { Section } from '../lib/types';
  let { id }: { id: string } = $props();
  let project = $state<{ section: Section; participants: { id: string; display_name: string; handle: string; kind: string; level: string }[] } | null>(null);
  let error = $state('');
  let busy = $state(false);
  let whiteboard = $state('');
  let editing = $state(false);
  let listName = $state('');
  const section = $derived(board.sections.find(s => s.id === id));
  const lists = $derived(board.listsOf(id).filter(list => list.kind !== 'drawer'));
  const work = $derived(myWork(
    board.plainItems.filter(item => lists.some(list => list.id === item.list_id)),
    board.me?.account.id ?? '', board.now,
  ));
  const attention = $derived([...new Map([
    ...work.askedOfMe, ...work.waitingOnMe, ...Object.values(work.buckets).flat(),
  ].map(item => [item.id, item])).values()]);
  const canEdit = $derived(atLeast(sectionLevel(board.me?.access, id), 'edit'));
  let generation = 0;
  async function load() {
    const gen = ++generation;
    try {
      const result = await get<typeof project>(`/sections/${id}/project`);
      if (gen === generation) { project = result; error = ''; }
    } catch (e) { if (gen === generation) { project = null; error = String(e); } }
  }
  $effect(() => { void id; void load(); });
  const off = board.onEvent(ev => {
    if (ev.type.startsWith('share.') || ev.type.startsWith('account.') || ev.type.startsWith('section.')) void load();
  });
  onDestroy(() => { generation++; off(); });
  async function createList() {
    if (busy || !listName.trim()) return;
    busy = true;
    const made = await board.createList(id, listName.trim());
    busy = false;
    if (made) { listName = ''; location.hash = `#/list/${made.id}`; }
  }
  async function saveBoard() {
    if (busy) return;
    busy = true;
    try {
      await patch(`/sections/${id}`, { whiteboard_url: whiteboard.trim() });
      editing = false; await load(); await board.reloadStructure();
    } catch (e) { error = String(e); }
    finally { busy = false; }
  }
</script>

<div class="project-home" aria-label="Project home">
  <div class="project-intro">
    <div><strong>Project home</strong><p class="muted small">One section for work, ideas, docs and chat. Private lists keep their own access rules.</p></div>
    <a
      class="chip-btn"
      href="#/section/{id}/chat"
      onclick={(e) => {
        // A wide screen opens the chat panel on this section and stays here; a phone goes to the Chat tab.
        if (!chatPanel.fits || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        chatPanel.show(`section:${id}`);
      }}>Open chat</a
    >
    {#if safeWhiteboard(project?.section.whiteboard_url)}<a class="chip-btn" href={project?.section.whiteboard_url ?? ''} target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">Open whiteboard ↗</a>{/if}
  </div>
  {#if error}<p role="alert">{error}</p>{/if}
  <section aria-label="Your project attention">
    <h3>Your attention in this project</h3>
    <p class="muted small">{work.openCount} owned tasks · {work.askedOfMe.length} tasks with open Asks · {work.waitingOnMe.length} waiting on you. Document requests are under Needs you in Docs & files below.</p>
    {#each attention.slice(0, 5) as item (item.id)}
      <a class="project-link" href="#/list/{item.list_id}?task={item.id}">{item.title}<span class="muted">{work.askedOfMe.some(i => i.id === item.id) ? 'Asked of you' : item.waiting_on_account_id === board.me?.account.id ? 'Waiting on you' : 'Owned by you'}</span></a>
    {:else}<p class="muted small">No owned tasks or open task requests for you here.</p>{/each}
    {#if attention.length > 5}<details><summary>{attention.length - 5} more in this project</summary>{#each attention.slice(5) as item (item.id)}<a class="project-link" href="#/list/{item.list_id}?task={item.id}">{item.title}</a>{/each}</details>{/if}
  </section>
  <div class="project-grid">
    <section aria-label="Project work and ideas"><h3>Work & ideas</h3>
      {#each lists as list (list.id)}<a class="project-link" href="#/list/{list.id}">{list.name} <span class="muted">{board.openCount(list.id)} open</span></a>{/each}
      {#if !lists.length}<p class="muted small">Start with a Work or Ideas list. Nothing is created automatically.</p>{/if}
      {#if canEdit}<form onsubmit={(e) => { e.preventDefault(); void createList(); }}><label>New project list<input aria-label="New project list" bind:value={listName} placeholder="Work or Ideas" maxlength="100" /></label><button class="chip-btn" disabled={busy || !listName.trim()}>Create list</button></form>{/if}
    </section>
    <DocsHub sectionId={id} {canEdit} />
    <section aria-label="Project participants"><h3>Participants</h3>
      {#each project?.participants ?? [] as person (person.id)}<p class="small">{person.display_name} <span class="muted">@{person.handle} · {person.kind} · {person.level}</span></p>{/each}
      {#if board.canManage}<button class="chip-btn" onclick={() => board.openShare('section', id)}>Share & invite</button>{/if}
      <p class="muted small">Section access only; private lists may have different participants.</p>
    </section>
  </div>
  {#if canEdit}
    <details><summary>External whiteboard settings</summary>
      <p class="muted small">A link, not a Slate canvas. Manage whiteboard permissions separately; opening it does not grant access.</p>
      {#if !editing}<button class="chip-btn" onclick={() => { editing = true; whiteboard = project?.section.whiteboard_url ?? ''; }}>Edit whiteboard link</button>
      {:else}<form onsubmit={(e) => { e.preventDefault(); void saveBoard(); }}><label>Whiteboard HTTPS URL<input type="url" aria-label="Whiteboard HTTPS URL" bind:value={whiteboard} placeholder="https://…" maxlength="2048" /></label><button class="chip-btn" disabled={busy || (!!whiteboard && !safeWhiteboard(whiteboard))}>Save whiteboard</button><button type="button" class="link" onclick={() => editing = false}>Cancel</button></form>{/if}
    </details>
  {/if}
</div>

<style>
  .project-home { border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin: 0 16px 12px; }
  .project-intro { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
  .project-intro > div { flex: 1 1 240px; }
  .project-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
  .project-grid section { min-width: 0; }
  .project-link { display: flex; justify-content: space-between; gap: 8px; padding: 6px 0; overflow-wrap: anywhere; }
  h3 { font-size: 14px; margin: 12px 0; }
  form { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; margin-top: 8px; }
  label { display: grid; flex: 1 1 150px; gap: 4px; font-size: 12px; min-width: 0; }
  input { width: 100%; box-sizing: border-box; }
  details { margin-top: 12px; }
  @media (max-width: 720px) { .project-grid { grid-template-columns: 1fr; gap: 4px; } .project-home { margin: 0 8px 12px; } }
</style>
