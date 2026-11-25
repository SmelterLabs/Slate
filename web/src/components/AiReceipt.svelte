<script lang="ts">
  // Floating translucent confirmation bubble after AI add: shows what was filed, which list
  // Claude routed it to, due/recurrence details, and lets you move it to another list, open it,
  // or undo right from the bubble.
  import { shortDate } from '../lib/format';
  import { board } from '../lib/store.svelte';
  import Icon from './Icon.svelte';

  const receipt = $derived(board.aiReceipt);
  const item = $derived(receipt ? board.items[receipt.itemId] ?? null : null);
  const currentListId = $derived(item?.list_id ?? receipt?.listId ?? '');
  const rule = $derived(item?.recurrence_id ? board.recurrences[item.recurrence_id] ?? null : null);
  const dueLabel = $derived(item?.due_at ? shortDate(item.due_at) : null);

  const choices = $derived(
    board.sections
      .map((s) => ({
        section: s,
        lists: board.listsOf(s.id).filter((l) => board.levelOn(l.id) === 'edit'),
      }))
      .filter((c) => c.lists.length),
  );

  let moving = $state(false);

  async function moveTo(listId: string) {
    if (!receipt || !listId || listId === currentListId) return;
    moving = true;
    await board.moveItemToList(receipt.itemId, listId);
    moving = false;
  }
</script>

{#if receipt}
  <div
    class="ai-receipt"
    role="status"
    aria-live="polite"
    onmouseenter={() => board.holdAiReceipt()}
    onmouseleave={() => board.releaseAiReceipt()}
    onfocusin={() => board.holdAiReceipt()}
    onfocusout={() => board.releaseAiReceipt()}
  >
    <span class="ai-receipt-icon" aria-hidden="true"><Icon name="sparkle" size={14} /></span>
    <button
      type="button"
      class="ai-receipt-title"
      title="Open task"
      onclick={() => {
        const id = receipt.itemId;
        board.dismissAiReceipt();
        void board.open(id);
      }}
    >
      {item?.title ?? receipt.title}
    </button>
    <span class="ai-receipt-arrow" aria-hidden="true">→</span>
    <select
      class="ai-receipt-list"
      aria-label="Routed list (change to move)"
      title="Routed to this list — pick another to move"
      value={currentListId}
      disabled={moving}
      onchange={(e) => void moveTo(e.currentTarget.value)}
    >
      {#each choices as c (c.section.id)}
        <optgroup label={c.section.name}>
          {#each c.lists as l (l.id)}
            <option value={l.id}>{c.section.name} · {l.name}</option>
          {/each}
        </optgroup>
      {/each}
    </select>
    {#if dueLabel}
      <span class="ai-receipt-meta">{dueLabel}</span>
    {/if}
    {#if rule}
      <span class="ai-receipt-meta" title={rule.description}><Icon name="repeat" size={11} /> {rule.description}</span>
    {:else if item?.recurrence_id}
      <span class="ai-receipt-meta"><Icon name="repeat" size={11} /> recurring</span>
    {/if}
    {#if receipt.fallback}
      <span class="ai-receipt-meta warn" title="AI was unreachable — parsed locally">local</span>
    {/if}
    <button
      type="button"
      class="ai-receipt-act"
      onclick={() => {
        board.dismissAiReceipt();
        void board.undoLast();
      }}
    >Undo</button>
    <button
      type="button"
      class="ai-receipt-close"
      aria-label="Dismiss"
      onclick={() => board.dismissAiReceipt()}
    ><Icon name="x" size={13} /></button>
  </div>
{/if}

<style>
  @keyframes ai-bubble-in {
    from {
      opacity: 0;
      translate: 0 10px;
    }
  }
  .ai-receipt {
    position: fixed;
    left: 50%;
    transform: translateX(-50%);
    bottom: 24px;
    z-index: 51;
    max-width: min(92vw, 760px);
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 8px 10px 8px 12px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--panel, #1b1d22) 82%, transparent);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid color-mix(in srgb, var(--line) 85%, transparent);
    color: var(--text);
    font-size: var(--fs-sm);
    box-shadow: var(--shadow-pop);
    animation: ai-bubble-in var(--dur-3, 180ms) var(--ease-spring, ease-out);
  }
  .ai-receipt-icon {
    display: inline-flex;
    color: var(--accent);
    flex: none;
  }
  .ai-receipt-title {
    font-weight: 600;
    color: var(--text);
    max-width: 240px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding: 0;
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
  }
  .ai-receipt-title:hover {
    text-decoration: underline;
  }
  .ai-receipt-arrow {
    color: var(--muted);
    flex: none;
  }
  .ai-receipt-list {
    font-size: 12px;
    font-weight: 600;
    color: var(--accent-ink, var(--text));
    background: color-mix(in srgb, var(--accent) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--accent) 32%, transparent);
    border-radius: 999px;
    padding: 2px 8px;
    cursor: pointer;
    max-width: 210px;
  }
  .ai-receipt-meta {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 11px;
    color: var(--muted);
    white-space: nowrap;
  }
  .ai-receipt-meta.warn {
    color: var(--warning, #d97706);
  }
  .ai-receipt-act {
    font-size: 12px;
    font-weight: 600;
    color: var(--accent);
    background: transparent;
    border: none;
    padding: 2px 6px;
    border-radius: 6px;
    cursor: pointer;
  }
  .ai-receipt-act:hover {
    background: color-mix(in srgb, var(--accent) 14%, transparent);
  }
  .ai-receipt-close {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--muted);
    background: transparent;
    border: none;
    padding: 3px;
    border-radius: 999px;
    cursor: pointer;
  }
  .ai-receipt-close:hover {
    color: var(--text);
    background: color-mix(in srgb, var(--text) 10%, transparent);
  }
</style>
