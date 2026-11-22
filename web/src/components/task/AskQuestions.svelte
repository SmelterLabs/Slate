<script lang="ts">
  // The questions a comment asks a person, in one tinted block at the top of the comment: the
  // first thing read, whatever the comment says around them, and shown in full when the comment
  // is folded. Nothing for a comment that asks nothing. Numbered when there are several. An
  // option is a live answer only on an open ask with one question, addressed to the viewer;
  // anyone else sees plain chips. Several questions are answered in one comment (the task
  // window's banner composes it).
  import { askQuestions } from '../../lib/conversation';
  import { board } from '../../lib/store.svelte';
  import { task } from '../../lib/task.svelte';
  import type { Comment } from '../../lib/types';

  let { comment }: { comment: Comment } = $props();

  const questions = $derived(comment.ask ? askQuestions(comment.ask) : []);
  const open = $derived(!comment.resolved_at);
  const forMe = $derived(!!comment.assignee_id && comment.assignee_id === task.me);
  const live = $derived(open && forMe && questions.length === 1);
  const forWhom = $derived(
    forMe ? 'you' : (board.accountName(comment.assignee_id ?? '') ?? 'someone'),
  );
</script>

{#if questions.length}
  <section
    class="aq"
    class:answered={!open}
    data-ask-box
    data-ask-state={open ? 'open' : 'answered'}
    data-open-ask-for-me={open && forMe ? comment.id : undefined}
  >
    <header class="aq-head">
      <span>{questions.length > 1 ? 'Questions' : 'Question'} for {forWhom}</span>
      <span class="aq-state">{open ? 'Open' : 'Answered'}</span>
    </header>
    <ol class="aq-list">
      {#each questions as q, i}
        <li class="aq-item">
          <p class="aq-q">
            {#if questions.length > 1}<span class="aq-n">{i + 1}.</span>{/if}
            <span data-ask-question>{q.question}</span>
          </p>
          {#if q.options.length}
            <div class="aq-opts">
              {#each q.options as option}
                {#if live}
                  <button class="aq-opt" data-answer-option={option} onclick={() => void task.answer(comment.id, option)}>{option}</button>
                {:else}
                  <span class="aq-opt plain" data-ask-option>{option}</span>
                {/if}
              {/each}
            </div>
          {/if}
        </li>
      {/each}
    </ol>
  </section>
{/if}

<style>
  .aq {
    --tone: var(--prio-high);
    margin: 0 0 8px;
    padding: 8px 10px 10px;
    border: 1px solid color-mix(in srgb, var(--tone) 40%, transparent);
    border-left: 3px solid var(--tone);
    border-radius: 8px;
    background: color-mix(in srgb, var(--tone) 10%, var(--panel));
  }
  .aq.answered {
    --tone: var(--muted);
    background: color-mix(in srgb, var(--text) 4%, var(--panel));
  }
  .aq-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 6px;
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .aq-state {
    flex: none;
    padding: 0 7px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--tone) 45%, transparent);
    color: var(--text);
    text-transform: none;
    letter-spacing: 0;
  }
  .aq-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .aq-q {
    display: flex;
    gap: 6px;
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    line-height: 1.4;
    color: var(--text);
    overflow-wrap: anywhere;
  }
  .aq-n {
    flex: none;
    font-variant-numeric: tabular-nums;
  }
  .aq-opts {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 5px;
  }
  .aq-opt {
    display: inline-flex;
    align-items: center;
    min-height: 28px;
    padding: 4px 10px;
    border-radius: 6px;
    border: 1px solid var(--line);
    font: inherit;
    font-size: 12px;
    font-weight: 600;
    color: var(--text);
  }
  button.aq-opt {
    cursor: pointer;
    background: color-mix(in srgb, var(--prio-high) 14%, var(--panel));
  }
  button.aq-opt:hover {
    border-color: var(--prio-high);
  }
  .aq-opt.plain {
    color: var(--muted);
    background: none;
    font-weight: 500;
  }
</style>
