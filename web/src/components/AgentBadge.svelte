<script lang="ts">
  // An agent's live report on an item: "My Agent · Working · 3 min ago" behind a robot icon (a percent and bar only when the
  // agent reported real progress), with the note as a
  // tooltip (card) or underneath (task window). Working pulses; blocked is red; review amber.
  // It is a flag over the history (AGENTS.md rule 4), so it sits beside the status, never as one.
  // Blocked reads "Waiting on Alex" when the agent named a person, "Agent blocked" when it named
  // nobody (lib/words.ts: one word per mechanism); a working badge shows how
  // long since the agent last reported, so a stalled run is visible on the card.
  import { agents, STATE_LABEL } from '../lib/agents.svelte';
  import { badgeLabel, safeAgentUrl, shortAge, shownProgress } from '../lib/conversation';
  import { ago } from '../lib/format';
  import { board } from '../lib/store.svelte';
  import { task } from '../lib/task.svelte';
  import type { AgentFlag, Item } from '../lib/types';
  import Icon from './Icon.svelte';

  let { flag, item = null, full = false }: { flag: AgentFlag; item?: Item | null; full?: boolean } = $props();

  const who = $derived(
    board.accounts[flag.by]?.display_name ?? agents.entry(flag.by)?.account.display_name ?? 'An agent',
  );
  const when = $derived(ago(flag.at, board.now));
  // A "working" report nobody has refreshed for six hours is probably abandoned: dim it.
  const stale = $derived(
    flag.state === 'working' && board.now.getTime() - new Date(flag.at).getTime() > 6 * 3600_000,
  );
  const pct = $derived(shownProgress(flag.progress));
  const label = $derived(badgeLabel(flag, item, (id) => board.accountName(id), STATE_LABEL));
  const waitingOnMe = $derived(
    flag.state === 'blocked' && !!item?.waiting_on_account_id && item.waiting_on_account_id === board.me?.account.id,
  );
  // Only a scheme the server accepts (http(s), plus a desktop app's own when configured): the
  // server checks too, but a link is never trusted blind.
  const safeUrl = $derived(safeAgentUrl(flag.url, board.me?.link_schemes));
  // A desktop app's link (myapp://...) is handed to the OS in this tab; a web link opens a new one.
  const newTab = $derived(!!safeUrl && /^https?:/i.test(safeUrl));
  const tip = $derived(
    `${who}: ${label}${pct != null ? ` (${pct}%)` : ''} · ${when}` +
      (flag.note ? `\n${flag.note}` : ''),
  );
</script>

<span
  class="agent-badge state-{flag.state}"
  class:full
  class:stale
  class:waiting-me={waitingOnMe}
  title={tip}
  data-agent-state={flag.state}
  data-agent-label={label}
>
  <span class="ab-row">
    <span class="dot" aria-hidden="true"></span>
    <span class="bot" aria-hidden="true"><Icon name="robot" size={12} /></span>
    <span class="who">{who}</span>
    <span class="sep">·</span>
    <span class="what">{label}{#if pct != null}&nbsp;{pct}%{/if}</span>
    {#if full}<span class="sep">·</span><span class="when">{when}</span>{:else if flag.state === 'working'}<span
        class="sep">·</span><span class="when" data-agent-age>{shortAge(flag.at, board.now)}</span>{/if}
    {#if safeUrl}<span class="sep">·</span><a
        class="open"
        href={safeUrl}
        target={newTab ? '_blank' : null}
        rel="noopener noreferrer"
        title="Open where {who} is working"
        onclick={(e) => e.stopPropagation()}>Open ↗</a
      >{/if}
  </span>
  {#if full && flag.note}<span class="note">{flag.note}</span>{/if}
  {#if full && item && flag.state === 'review'}
    <button
      class="reviewed"
      data-mark-reviewed
      title="You've read {who}'s answer: clear Ready for review. Changing the card's status clears it too; closing this window does not."
      onclick={(e) => {
        e.stopPropagation();
        void task.clearAgentFlag(item.id);
      }}>✓ Mark reviewed</button
    >
  {/if}
  {#if full && item && flag.state === 'blocked'}
    <button
      class="reviewed"
      data-clear-blocked
      title="Take {who}'s blocked badge off this card. On an expired run this confirms the run stopped, so {who} can pick the card up again. It does not change who the card waits on."
      onclick={(e) => {
        e.stopPropagation();
        void task.clearAgentFlag(item.id);
      }}>✕ Clear</button
    >
  {/if}
  {#if pct != null}
    <span class="bar" aria-hidden="true"><span style:width="{pct}%"></span></span>
  {/if}
</span>

<style>
  .agent-badge {
    --tone: var(--accent);
    display: inline-flex;
    flex-direction: column;
    gap: 3px;
    max-width: 100%;
    padding: 3px 8px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--tone) 45%, transparent);
    background: color-mix(in srgb, var(--tone) 12%, transparent);
    color: var(--text);
    font-size: 12px;
    line-height: 1.3;
  }
  .agent-badge.full {
    border-radius: var(--radius);
    padding: 8px 12px;
    font-size: 13px;
    width: 100%;
    box-sizing: border-box;
  }
  .state-blocked {
    --tone: var(--danger);
  }
  .state-review {
    --tone: var(--prio-high);
  }
  .stale {
    opacity: 0.55;
  }
  .waiting-me {
    font-weight: 600;
  }
  /* not .row: that global class is the List layout's row (app.css) */
  .ab-row {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    min-width: 0;
    white-space: nowrap;
  }
  .who {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .sep,
  .when {
    color: var(--muted);
  }
  .open {
    color: var(--tone);
    font-weight: 600;
    text-decoration: none;
  }
  .open:hover {
    text-decoration: underline;
  }
  .bot {
    display: inline-flex;
    color: var(--tone);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--tone);
    flex: none;
  }
  .state-working:not(.stale) .dot {
    animation: pulse 1.6s ease-in-out infinite;
  }
  @keyframes pulse {
    0%,
    100% {
      box-shadow: 0 0 0 0 color-mix(in srgb, var(--tone) 70%, transparent);
    }
    50% {
      box-shadow: 0 0 0 4px color-mix(in srgb, var(--tone) 0%, transparent);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .state-working .dot {
      animation: none;
    }
  }
  .reviewed {
    align-self: flex-start;
    margin-top: 4px;
    padding: 4px 10px;
    border-radius: 6px;
    border: 1px solid color-mix(in srgb, var(--tone) 60%, transparent);
    background: color-mix(in srgb, var(--tone) 18%, transparent);
    color: var(--text);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }
  .reviewed:hover {
    background: color-mix(in srgb, var(--tone) 30%, transparent);
  }
  .note {
    color: var(--muted);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .bar {
    display: block;
    height: 3px;
    border-radius: 2px;
    background: color-mix(in srgb, var(--tone) 20%, transparent);
    overflow: hidden;
  }
  .bar > span {
    display: block;
    height: 100%;
    background: var(--tone);
    transition: width 0.4s ease;
  }
</style>
