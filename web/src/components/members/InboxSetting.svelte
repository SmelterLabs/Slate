<script lang="ts">
  // Workspace default filing list (GET/PUT /settings/inbox), shown on the Members page for admins.
  import { onDestroy } from 'svelte';
  import { get, put } from '../../lib/api';
  import { board } from '../../lib/store.svelte';

  let inboxId = $state<string | null>(null);
  let loaded = $state(false);
  let busy = $state(false);

  const choices = $derived(
    board.sections
      .map((s) => ({
        section: s,
        lists: board.listsOf(s.id).filter((l) => !l.reference && board.levelOn(l.id) === 'edit'),
      }))
      .filter((c) => c.lists.length),
  );

  async function load() {
    try {
      const res = await get<{ inbox_list_id: string | null }>('/settings/inbox');
      inboxId = res.inbox_list_id;
      loaded = true;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  $effect(() => {
    void load();
  });

  const off = board.onEvent((ev) => {
    if (ev.type === 'settings.changed' && ev.field === 'inbox_list_id') void load();
  });
  onDestroy(off);

  async function save(next: string) {
    const value = next || null;
    busy = true;
    try {
      const res = await put<{ inbox_list_id: string | null }>('/settings/inbox', { inbox_list_id: value });
      inboxId = res.inbox_list_id;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
      await load();
    } finally {
      busy = false;
    }
  }
</script>

{#if loaded}
  <section class="agent-rules" aria-label="Default list">
    <div>
      <strong>Default list</strong>
      <span class="muted small">
        Where AI add and tasks filed with no list land when nothing more specific matches.
      </span>
    </div>
    <select
      aria-label="Default list"
      value={inboxId ?? ''}
      disabled={busy}
      onchange={(e) => void save(e.currentTarget.value)}
    >
      <option value="">None (pick a list each time)</option>
      {#each choices as c (c.section.id)}
        <optgroup label={c.section.name}>
          {#each c.lists as l (l.id)}
            <option value={l.id}>{c.section.name} · {l.name}</option>
          {/each}
        </optgroup>
      {/each}
    </select>
  </section>
{/if}
