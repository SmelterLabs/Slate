<script lang="ts">
  import { matchList, parseQuickAdd } from '../lib/quickadd';
  import { priorityLabel } from '../lib/priority';
  import { draftKey } from '../lib/drafts';
  import { persisted } from '../lib/drafts.svelte';
  import { describeRrule, shortDate } from '../lib/format';
  import { board } from '../lib/store.svelte';
  import { share } from '../lib/share.svelte';
  import type { Scope } from '../lib/views';
  import AiToggle from './AiToggle.svelte';

  // In a list the target is that list; in wider scopes a picker sits next to the input and
  // remembers its choice per view.
  let { scope, viewKey }: { scope: Scope; viewKey: string } = $props();

  const draft = persisted(() => draftKey.quickAdd());
  let busy = $state(false);
  let picked = $state<string | null>(null);
  // AI add (POST /items/ai): the words go to the model, so no quick-add tokens are read.
  const ai = $derived(board.aiAddOn && board.aiReady);
  const parsed = $derived(parseQuickAdd(ai ? '' : draft.value));
  // "~bills" sends the item to that list, whatever the picker says.
  const named = $derived(parsed.list ? matchList(parsed.list, Object.values(board.lists)) : null);
  const listId = $derived(named?.id ?? picked ?? board.quickAddList(viewKey, scope));
  const choices = $derived(
    scope.kind === 'list'
      ? []
      : board.sections
          .filter((s) => scope.kind === 'everything' || s.id === scope.id)
          .map((s) => ({ section: s, lists: board.listsOf(s.id).filter((l) => board.levelOn(l.id) === 'edit') }))
          .filter((c) => c.lists.length),
  );

  $effect(() => {
    void viewKey;
    picked = null;
  });

  // A share from the phone's share sheet: the title lands in the box once, the link (and any
  // text) rides along as the description until the item is filed or the chip is dropped.
  let input = $state<HTMLInputElement>();
  $effect(() => {
    if (share.line === null) return;
    draft.value = share.line;
    share.line = null;
    input?.focus();
  });
  const shareNote = $derived(share.body?.split('\n')[0] ?? '');

  function whenLabel(v: string): string {
    const [date, time] = v.split('T');
    const day = shortDate(`${date}T12:00:00Z`);
    if (!time) return day;
    const [h, m] = time.split(':').map(Number);
    const hour = h % 12 || 12;
    return `${day} ${hour}${m ? `:${String(m).padStart(2, '0')}` : ''}${h < 12 ? 'am' : 'pm'}`;
  }

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (ai) {
      const text = [draft.value.trim(), share.body ?? ''].filter(Boolean).join('\n\n');
      if (!text || busy || !listId) return;
      busy = true;
      if (await board.aiAdd(listId, text)) {
        draft.value = '';
        share.body = null;
      }
      busy = false;
      return;
    }
    if (!parsed.title || busy || !listId || (parsed.list && !named)) return;
    busy = true;
    const extra = share.body ? { body_md: share.body } : {};
    if (await board.quickAdd(listId, parsed, extra)) {
      draft.value = '';
      share.body = null;
    }
    busy = false;
  }
</script>

<form class="quick-add" class:wide={scope.kind !== 'list'} onsubmit={submit}>
  <div class="qa-row">
    {#if scope.kind !== 'list'}
      <select
        aria-label="Add to list"
        value={listId ?? ''}
        onchange={(e) => {
          picked = e.currentTarget.value;
          board.rememberQuickAddList(viewKey, picked);
        }}
      >
        {#each choices as c (c.section.id)}
          <optgroup label={c.section.name}>
            {#each c.lists as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
          </optgroup>
        {/each}
      </select>
    {/if}
    <input
      id="quick-add"
      aria-label="Quick add"
      placeholder={ai
        ? 'Describe the task: what, who owns it, when… AI fills in the rest   (c)'
        : 'Add an item…  tomorrow 4pm  #tag  !high  @my-agent  ~list  every monday   (c)'}
      bind:value={draft.value}
      bind:this={input}
      disabled={busy}
    />
    <AiToggle />
  </div>
  {#if draft.value.trim() || share.body}
    <div class="qa-preview" aria-live="polite">
      {#if share.body}
        <span class="chip" title={share.body}>+ note: {shareNote}</span>
        <button type="button" class="link small" onclick={() => (share.body = null)}>drop the note</button>
      {/if}
      {#if ai}
        <span class="chip">✨ AI will set the title, owner, dates and details</span>
      {/if}
      {#if parsed.priority}<span class="chip prio-{parsed.priority}">{priorityLabel(parsed.priority)}</span>{/if}
      {#each parsed.tags as t (t)}<span class="chip">#{t}</span>{/each}
      {#each parsed.assignees as a (a)}<span class="chip">@{a}</span>{/each}
      {#if parsed.due}<span class="chip">due {whenLabel(parsed.due)}</span>{/if}
      {#if parsed.start}<span class="chip">starts {whenLabel(parsed.start)}</span>{/if}
      {#if parsed.rrule}<span class="chip">↻ {describeRrule(parsed.rrule)}</span>{/if}
      {#if parsed.list}
        <span class="chip" class:warn={!named}>{named ? `→ ${named.name}` : `no list “${parsed.list}”`}</span>
      {/if}
    </div>
  {/if}
</form>
