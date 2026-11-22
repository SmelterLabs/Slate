<script lang="ts">
  // The task window's "your move" banner, at the top: the card waits on the viewer (`wait`), or an
  // agent finished and asks the viewer to check its work (`review`). It carries the agent badge's
  // parts (age, Open link, Clear), so the window leaves that badge out.
  //  - One question: the question is the heading, its options answer in one tap.
  //  - Several: each question numbered, with its options as chips and a line to type in; "Send
  //    answers" posts them as one ordinary comment (a plain comment by the person asked is the
  //    answer), so it waits for every answer.
  //  - A manual wait: who waits on the viewer and a way to reply in the thread.
  //  - A review: what to check is the heading; Mark reviewed clears the flag.
  import { safeAgentUrl } from '../../lib/conversation';
  import { ago } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import { task } from '../../lib/task.svelte';
  import type { AgentFlag, AskQuestion, ItemStanding } from '../../lib/types';
  import Icon from '../Icon.svelte';

  type Wait = {
    structured: boolean;
    by: string | null;
    commentId: string | null;
    question: string | null;
    questions: AskQuestion[];
  };

  let {
    itemId,
    wait,
    review,
    flag,
  }: { itemId: string; wait: Wait | null; review: ItemStanding | null; flag: AgentFlag | null } = $props();

  const by = $derived(wait ? wait.by : (flag?.by ?? null));
  const who = $derived((by && board.accountName(by)) || 'Someone');
  const url = $derived(flag ? safeAgentUrl(flag.url, board.me?.link_schemes) : null);
  const several = $derived(!!wait?.structured && wait.questions.length > 1);

  // Several questions: a chip picked or a line typed per question; typing wins over a chip and
  // picking a chip clears the line. Keyed by the asking comment, so a new ask starts empty.
  let picked = $state<Record<string, string>>({});
  let typed = $state<Record<string, string>>({});
  let sending = $state(false);
  const key = (i: number) => `${wait?.commentId}:${i}`;
  const answerTo = (i: number) => typed[key(i)]?.trim() || picked[key(i)] || '';
  const ready = $derived(several && wait!.questions.every((_, i) => !!answerTo(i)));

  async function sendAnswers() {
    if (!wait || !ready) return;
    const body = wait.questions.map((q, i) => {
      const answer = answerTo(i).replace(/[\\`*_[\]~:]/g, '\\$&');
      return `${i + 1}. ${q.question} **${answer}**`;
    }).join('\n');
    sending = true;
    if (await task.comment(itemId, body)) {
      picked = {};
      typed = {};
    }
    sending = false;
  }
</script>

{#snippet meta()}
  {#if flag}
    <span class="wq-meta" data-waiting-age>· {ago(flag.at, board.now)}</span>
    {#if url}<span class="wq-meta">·</span><a
        class="wq-open"
        data-waiting-open
        href={url}
        target={/^https?:/i.test(url) ? '_blank' : null}
        rel="noopener noreferrer"
        title="Open where the agent is working">Open ↗</a
      >{/if}
  {/if}
{/snippet}

{#if wait}
  <div class="turn waiting" data-banner="waiting">
    {#if several}
      <p class="wq-heading">{who} needs {wait.questions.length} answers from you</p>
      <div class="wq-head wq-sub"><Icon name="hourglass" size={13} /><span>Answer each, then send them together</span>{@render meta()}</div>
      <ol class="wq-list">
        {#each wait.questions as q, i}
          <li class="wq-item">
            <p class="wq-q"><span>{i + 1}.</span> <span data-ask-question>{q.question}</span></p>
            {#if q.options.length}
              <div class="wq-chips">
                {#each q.options as option}
                  <button
                    class="wq-chip"
                    aria-pressed={picked[key(i)] === option && !typed[key(i)]?.trim()}
                    data-pick-option={option}
                    onclick={() => {
                      picked[key(i)] = option;
                      typed[key(i)] = '';
                    }}>{option}</button
                  >
                {/each}
              </div>
            {/if}
            <input
              class="wq-type"
              aria-label="Answer to question {i + 1}"
              placeholder={q.options.length ? 'or type an answer' : 'Type an answer'}
              bind:value={() => typed[key(i)] ?? '', (v) => (typed[key(i)] = v)}
            />
          </li>
        {/each}
      </ol>
    {:else if wait.structured}
      <p class="wq-heading" data-waiting-question data-ask-question>{wait.question}</p>
      <div class="wq-head wq-sub">
        <Icon name="hourglass" size={13} />
        <span>{who} asks</span>
        {@render meta()}
      </div>
    {:else}
      <div class="wq-head">
        <Icon name="hourglass" size={13} />
        <strong>{who} is waiting on you</strong>
        {@render meta()}
      </div>
    {/if}
    <div class="wq-actions">
      {#if several}
        <button class="primary" data-send-answers disabled={!ready || sending} onclick={sendAnswers}>Send answers</button>
      {:else if wait.structured}
        {#each wait.questions[0]?.options ?? [] as option}
          <button
            class="primary"
            data-answer-option={option}
            onclick={() => wait.commentId && void task.answer(wait.commentId, option)}>{option}</button
          >
        {/each}
      {/if}
      <button class:primary={!several} data-answer onclick={() => task.reveal(wait.commentId)}>{wait.structured ? 'Answer' : 'Reply'}</button>
      <button class="link" data-not-waiting onclick={() => board.update(itemId, { waiting_on_account_id: null })}
        >Not waiting on me</button
      >
      {#if flag}
        <button
          class="link"
          data-clear-blocked
          title="Take the agent's blocked badge off this card. It does not change who the card waits on."
          onclick={() => task.clearAgentFlag(itemId)}>✕ Clear badge</button
        >
      {/if}
    </div>
  </div>
{:else if review}
  <div class="turn review" data-banner="review">
    <p class="wq-heading" data-review-check>{review.line}</p>
    <div class="wq-head wq-sub">
      <Icon name="eye" size={13} />
      <span>{who} finished and asks you to check</span>
      {@render meta()}
    </div>
    <div class="wq-actions">
      <button
        class="primary"
        data-mark-reviewed
        title="You've checked {who}'s work: clear Ready for review. Changing the card's status clears it too."
        onclick={() => task.clearAgentFlag(itemId)}>✓ Mark reviewed</button
      >
    </div>
  </div>
{/if}

<style>
  .turn {
    --tone: var(--danger);
    margin-bottom: 12px;
    padding: 8px 12px;
    border: 1px solid color-mix(in srgb, var(--tone) 40%, transparent);
    border-left: 3px solid var(--tone);
    border-radius: 8px;
    background: color-mix(in srgb, var(--tone) 8%, transparent);
    font-size: 13px;
  }
  .turn.review {
    --tone: var(--prio-high);
  }
  .wq-heading {
    margin: 0 0 4px;
    font-size: 15px;
    font-weight: 700;
    color: var(--text);
    overflow-wrap: anywhere;
  }
  .wq-head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
    color: var(--tone);
  }
  .wq-sub {
    margin-bottom: 8px;
    font-size: 12.5px;
    color: var(--muted);
  }
  .wq-meta {
    color: var(--muted);
  }
  .wq-open {
    color: var(--tone);
    font-weight: 600;
    text-decoration: none;
  }
  .wq-open:hover {
    text-decoration: underline;
  }
  .wq-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin: 0 0 12px;
    padding: 0;
    list-style: none;
  }
  .wq-q {
    margin: 0 0 6px;
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    overflow-wrap: anywhere;
  }
  .wq-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 6px;
  }
  .wq-chip {
    min-height: 28px;
    padding: 3px 12px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: var(--panel);
    color: var(--text);
    font: inherit;
    font-size: 12.5px;
    cursor: pointer;
  }
  .wq-chip[aria-pressed='true'] {
    border-color: var(--tone);
    background: color-mix(in srgb, var(--tone) 16%, var(--panel));
    font-weight: 600;
  }
  .wq-type {
    width: 100%;
    max-width: 420px;
    box-sizing: border-box;
    font-size: 13px;
  }
  .wq-actions {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .wq-actions button {
    min-height: 28px;
  }
</style>
