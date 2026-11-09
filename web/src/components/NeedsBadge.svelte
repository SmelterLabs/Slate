<script lang="ts">
  // The "Needs you" signal on every screen (lib/attention.ts). `float`: a pill docked
  // bottom-right that opens the list (desktop); `top`: the phone top bar's pill, which goes to
  // My Work. Colored by the most urgent reason: red while anything is blocked on you.
  import { countBy, needLabel, NEEDS, type Need } from '../lib/attention';
  import { board } from '../lib/store.svelte';
  import NeedsPanel from './work/NeedsPanel.svelte';

  let { mode }: { mode: 'top' | 'float' } = $props();

  const list = $derived(board.needsMe);
  const n = $derived(list.length);
  const counts = $derived(countBy(list));
  // The most urgent reason colors the badge: red while anything is blocked on you.
  const top = $derived<Need | null>(list[0]?.need ?? null);
  const tone = $derived(top === 'waiting' || top === 'blocked' ? 'hot' : top === 'asked' ? 'ask' : 'review');
  const me = $derived(board.me?.account.display_name);
  const parts = $derived(NEEDS.filter((x) => counts[x.key]).map((x) => `${counts[x.key]} ${needLabel(x.key, me)}`));
  const tip = $derived(n ? `Needs you: ${parts.join(', ')}` : 'Nothing needs you');
  let open = $state(false);
  let root = $state<HTMLElement | null>(null);

  // Closes on Escape, on a click outside, and when a card in it is opened.
  function outside(e: MouseEvent) {
    if (open && root && !root.contains(e.target as Node)) open = false;
  }
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && (open = false)} onclick={outside} />

{#if mode === 'top'}
  {#if n}
    <a class="nb-top tone-{tone}" href="#/home" aria-label={tip} title={tip} data-needs-badge={n}>
      <i></i>{n}
    </a>
  {/if}
{:else if mode === 'float'}
  {#if n}
    <div class="nb-float" bind:this={root}>
      {#if open}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <div
          class="nb-pop"
          role="dialog"
          tabindex="-1"
          aria-label="Needs you"
          onclick={(e) => {
            if ((e.target as HTMLElement).closest('.nd-main')) open = false;
          }}
        >
          <NeedsPanel layout="groups" />
        </div>
      {/if}
      <button class="nb-pill tone-{tone}" aria-expanded={open} title={tip} data-needs-badge={n} onclick={() => (open = !open)}>
        <span class="nb-dot"></span>
        <b>{n}</b>&nbsp;need{n === 1 ? 's' : ''} you
        <span class="nb-segs">{#each NEEDS as x (x.key)}{#if counts[x.key]}<i class="need-{x.key}"></i>{/if}{/each}</span>
      </button>
    </div>
  {/if}
{/if}

<style>
  .need-waiting,
  .need-blocked {
    --tone: var(--danger);
  }
  .need-asked {
    --tone: var(--accent);
  }
  .need-review {
    --tone: var(--prio-high);
  }
  .tone-hot {
    --lead: var(--danger);
  }
  .tone-ask {
    --lead: var(--accent);
  }
  .tone-review {
    --lead: var(--prio-high);
  }
  i {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--tone);
  }
  .nb-segs {
    display: inline-flex;
    align-items: center;
    gap: 3px;
  }
  /* phone top bar */
  .nb-top {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 9px;
    border-radius: 999px;
    font-size: 12.5px;
    font-weight: 700;
    color: var(--text);
    text-decoration: none;
    background: color-mix(in srgb, var(--lead) 20%, transparent);
    border: 1px solid color-mix(in srgb, var(--lead) 50%, transparent);
  }
  .nb-top i {
    --tone: var(--lead);
    width: 7px;
    height: 7px;
  }
  /* floating */
  .nb-float {
    position: fixed;
    right: 18px;
    bottom: 18px;
    z-index: 40;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 8px;
  }
  .nb-pill {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 7px 14px 7px 12px;
    border-radius: 999px;
    font: inherit;
    font-size: 13px;
    color: var(--text);
    cursor: pointer;
    background: color-mix(in srgb, var(--lead) 16%, var(--raised));
    border: 1px solid color-mix(in srgb, var(--lead) 55%, transparent);
    box-shadow: var(--shadow-pop);
    backdrop-filter: blur(8px);
  }
  .nb-pill .nb-segs {
    margin-left: 8px;
  }
  .nb-dot {
    width: 8px;
    height: 8px;
    margin-right: 6px;
    border-radius: 50%;
    background: var(--lead);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--lead) 28%, transparent);
  }
  .nb-pop {
    width: min(480px, calc(100vw - 36px));
    max-height: 60vh;
    overflow: auto;
    border-radius: 12px;
    box-shadow: var(--shadow-pop);
  }
  .nb-pop :global(.needs) {
    margin: 0;
  }
  @media (max-width: 720px) {
    /* the phone has the top bar's pill */
    .nb-float {
      display: none;
    }
  }
</style>
