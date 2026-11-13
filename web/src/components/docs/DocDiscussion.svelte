<script lang="ts">
  // The side panel for talking about a document. Two tabs, one comment system:
  // - Discussion: the running conversation, in time order, with the document's activity
  //   (revisions uploaded, saves, moves, threads resolved) folded in as small grey lines a
  //   caret opens. Every comment says which revision it was written on.
  // - Review: threads pinned to a spot (a quoted span, a page, a point on a drawing), each
  //   with replies and Resolve; on a page a thread may carry a suggested replacement that
  //   Accept applies. A thread from an older revision folds as outdated.
  // The gesture picks the surface: select text or click the file to start a review thread;
  // type in the box at the bottom for discussion.
  import { tick } from 'svelte';
  import { highlightMentions, stamp } from '../../lib/activity';
  import { anchorLabel, docEventLine, feedRows, threadsOf, type Anchor, type DocComment, type Thread } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import { DRAFT_MAX_CHARS, draftKey, readDraft } from '../../lib/drafts';
  import { persisted } from '../../lib/drafts.svelte';
  import { ago } from '../../lib/format';
  import { renderMarkdown } from '../../lib/markdown';
  import { isSendKey, MentionPicker } from '../../lib/mentions.svelte';
  import { board } from '../../lib/store.svelte';
  import type { Account } from '../../lib/types';
  import Avatars from '../Avatars.svelte';
  import Icon from '../Icon.svelte';

  let { docId, isFile = false, viewedRevision, onclose }: { docId: string; isFile?: boolean; viewedRevision?: number; onclose: () => void } = $props();

  type Tab = 'discussion' | 'review';
  let tab = $state<Tab>('discussion');
  let showResolved = $state(false);
  let feed = $state<HTMLElement>();

  const me = $derived(board.me?.account.id ?? '');
  const myHandle = $derived(board.me?.account.handle ?? '');
  const name = (id: string) => board.accountName(id);
  const known = (h: string) => board.pickable.some((a) => a.handle === h);

  const discussion = $derived(threadsOf(docs.comments, 'discussion'));
  const review = $derived(threadsOf(docs.comments, 'review'));
  const openReview = $derived(review.filter((t) => !t.root.resolved_at));
  const shownReview = $derived(showResolved ? review : openReview);
  const rows = $derived(feedRows(discussion, docs.activity));
  const currentRev = $derived(isFile ? (viewedRevision ?? docs.current?.file?.revision_no ?? 0) : (docs.current?.version_no ?? 1));

  // A pending anchor (from a selection or a pin) opens the review tab with the form.
  $effect(() => {
    if (docs.pendingAnchor) {
      tab = 'review';
      tick().then(() => document.getElementById('review-body')?.focus());
    }
  });
  // A thread to focus (from a pin or a ?comment= link): switch tabs, show resolved, scroll.
  $effect(() => {
    const id = docs.focusComment;
    if (!id) return;
    const c = docs.comments.find((x) => x.id === id);
    if (!c) return;
    const rootId = c.parent_id ?? c.id;
    tab = c.surface;
    if (c.surface === 'review' && docs.comments.find((x) => x.id === rootId)?.resolved_at) showResolved = true;
    tick().then(() => {
      document.getElementById(`thread-${rootId}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      docs.focusComment = null;
    });
  });

  // --- composers ----------------------------------------------------------------------------

  const key = (field: string, context = '') => me ? draftKey.doc(me, docId, field, context) : null;
  // The panel can be closed and recreated while sending. Its obsolete instance must not
  // clear text written by the new one (or another tab) under the same persistent key.
  function unchangedStored(owner: string | null, sent: string) {
    const stored = owner ? readDraft(owner) : '';
    return !stored || stored === sent.slice(0, DRAFT_MAX_CHARS);
  }
  const body = persisted(() => key('discussion'));
  const ask = persisted(() => key('discussion-ask'));
  let sending = $state(false);
  let area = $state<HTMLTextAreaElement>();
  const picker = new MentionPicker(() => board.pickable);
  const askable = $derived(board.pickable.filter((a) => a.id !== me));
  function track() {
    if (area) picker.track(body.value, area.selectionStart);
  }
  function pickPerson(a: Account) {
    const out = area && picker.insert(body.value, area.selectionStart, a);
    if (!out) return;
    body.value = out.text;
    queueMicrotask(() => {
      area?.focus();
      area?.setSelectionRange(out.caret, out.caret);
    });
  }
  function keys(e: KeyboardEvent) {
    if (picker.keydown(e, pickPerson)) return;
    if (isSendKey(e)) {
      e.preventDefault();
      void send();
    }
  }
  async function send() {
    const text = body.value.trim();
    if (!text || sending) return;
    sending = true;
    const owner = key('discussion');
    const sent = body.value;
    const c = await docs.comment(docId, text, { assignee: ask.value || null, viewed_revision: viewedRevision });
    sending = false;
    if (c && owner === key('discussion') && body.value === sent && unchangedStored(owner, sent)) {
      body.value = '';
      ask.value = '';
      picker.close();
      await tick();
      feed?.scrollTo({ top: feed.scrollHeight });
    }
  }

  // Review: a new thread from the pending anchor.
  // Include the immutable viewed revision and anchor: never recover onto a different span.
  const reviewContext = $derived(JSON.stringify([docs.pendingRevision, docs.pendingAnchor]));
  const reviewKey = (field: string) => docs.pendingAnchor ? key(field, reviewContext) : null;
  const reviewBody = persisted(() => reviewKey('review'));
  const suggestion = persisted(() => reviewKey('suggestion'));
  const reviewAsk = persisted(() => reviewKey('review-ask'));
  const pageNo = persisted(() => reviewKey('review-page'));
  function cancelReview() {
    reviewBody.value = ''; suggestion.value = ''; reviewAsk.value = ''; pageNo.value = '';
    docs.pendingAnchor = null;
  }
  async function sendReview() {
    const text = reviewBody.value.trim();
    const anchor: Anchor = { ...(docs.pendingAnchor ?? {}) };
    if (isFile && pageNo.value.trim() && !anchor.page) anchor.page = Number(pageNo.value);
    if (!text || sending || !Object.keys(anchor).length) return;
    sending = true;
    const owner = reviewKey('review');
    const sent = reviewBody.value;
    const c = await docs.comment(docId, text, { anchor, suggestion_md: isFile ? undefined : suggestion.value, assignee: reviewAsk.value || null, viewed_revision: docs.pendingRevision ?? viewedRevision });
    sending = false;
    if (c && owner === reviewKey('review') && reviewBody.value === sent && unchangedStored(owner, sent)) {
      cancelReview();
      docs.focusComment = c.id;
    }
  }
  function startPageThread() {
    docs.startReview({}, viewedRevision);
  }

  // Replies, edits.
  let replyTo = $state<string | null>(null);
  const replyText = persisted(() => replyTo ? key('reply', replyTo) : null);
  async function sendReply(root: DocComment) {
    const text = replyText.value.trim();
    if (!text || sending) return;
    sending = true;
    const owner = key('reply', root.id);
    const sent = replyText.value;
    const c = await docs.comment(docId, text, { parent_id: root.id });
    sending = false;
    if (c && owner === key('reply', replyTo ?? '') && replyText.value === sent && unchangedStored(owner, sent)) {
      replyText.value = '';
      replyTo = null;
    }
  }
  let editing = $state<string | null>(null);
  let editText = $state('');
  async function saveEdit(c: DocComment) {
    const text = editText.trim();
    if (text && text !== c.body_md) await docs.editComment(c.id, text);
    editing = null;
  }

  const html = (c: DocComment) => highlightMentions(renderMarkdown(c.body_md), known, myHandle);
  const revLabel = (c: DocComment) => (isFile ? `rev ${c.anchor_revision}` : `v${c.anchor_revision}`);
  const foldedThreads = $derived(new Set(review.filter((t) => t.root.outdated && !t.root.resolved_at && docs.focusComment !== t.root.id).map((t) => t.root.id)));
  let unfolded = $state<Record<string, boolean>>({});
</script>

<aside class="doc-history doc-discussion" aria-label="Discussion">
  <header>
    <div class="dd-tabs" role="tablist">
      <button role="tab" aria-selected={tab === 'discussion'} class:active={tab === 'discussion'} onclick={() => (tab = 'discussion')}>
        Discussion{#if discussion.length}<span class="dd-count">{discussion.length}</span>{/if}
      </button>
      <button role="tab" aria-selected={tab === 'review'} class:active={tab === 'review'} onclick={() => (tab = 'review')}>
        Review{#if openReview.length}<span class="dd-count open">{openReview.length}</span>{/if}
      </button>
    </div>
    <button class="icon" aria-label="Close discussion" onclick={onclose}><Icon name="x" size={16} /></button>
  </header>

  {#if tab === 'discussion'}
    <div class="dd-feed" bind:this={feed} data-testid="discussion-feed">
      {#if !rows.length}
        <p class="muted pad small">Nothing yet. Say something about this {isFile ? 'file' : 'page'}; @mention someone to bring them in.</p>
      {/if}
      {#each rows as r, i (r.kind === 'thread' ? r.thread.root.id : `act-${i}-${r.events[0]?.id}`)}
        {#if r.kind === 'activity'}
          <details class="dd-activity" data-activity>
            <summary>
              <Icon name="chevronRight" size={11} />
              <span class="dd-act-line">{docEventLine(r.events[r.events.length - 1], name).text}{#if r.events.length > 1}<span class="muted"> · {r.events.length} changes</span>{/if}</span>
              <time class="muted" datetime={r.at}>{ago(r.events[r.events.length - 1].at, board.now)}</time>
            </summary>
            <ul>
              {#each r.events as ev (ev.id)}
                {@const line = docEventLine(ev, name)}
                <li><span>{line.text}</span>{#if line.detail}<span class="dd-act-detail">{line.detail}</span>{/if}<time class="muted" datetime={ev.at}>{stamp(ev.at, board.now)}</time></li>
              {/each}
            </ul>
          </details>
        {:else}
          {@render thread(r.thread, false)}
        {/if}
      {/each}
    </div>
    <div class="dd-compose">
      {#if picker.open}
        <ul class="dd-people" role="listbox">
          {#each picker.people as a, i (a.id)}
            <li><button role="option" aria-selected={i === picker.active} class:active={i === picker.active} onmousedown={(e) => e.preventDefault()} onclick={() => pickPerson(a)}>@{a.handle} <span class="muted">{a.display_name}</span></button></li>
          {/each}
        </ul>
      {/if}
      <textarea
        bind:this={area}
        bind:value={body.value}
        rows="2"
        placeholder={`Write about this ${isFile ? 'file' : 'page'}… @ to mention`}
        aria-label="Discussion comment"
        onkeydown={keys}
        onkeyup={track}
        onclick={track}
      ></textarea>
      <div class="dd-compose-row">
        <label class="dd-ask"><span class="muted small">Ask</span>
          <select bind:value={ask.value} aria-label="Ask someone to answer">
            <option value="">no one</option>
            {#each askable as a (a.id)}<option value={a.handle}>{a.display_name}</option>{/each}
          </select>
        </label>
        <span class="muted small">on {isFile ? `rev ${currentRev}` : `v${currentRev}`}</span>
        <button class="primary" disabled={!body.value.trim() || sending} onclick={() => void send()}>Send</button>
      </div>
    </div>
  {:else}
    <div class="dd-feed" data-testid="review-list">
      {#if docs.pendingAnchor}
        {@const a = docs.pendingAnchor}
        <form class="dd-new-thread" onsubmit={(e) => { e.preventDefault(); void sendReview(); }} data-testid="review-form">
          <p class="dd-anchor">
            <Icon name="comment" size={13} />
            {#if a.quote}<span>On “{a.quote.length > 80 ? `${a.quote.slice(0, 79)}…` : a.quote}”</span>
            {:else if a.x !== undefined}<span>On a spot{a.page ? ` on page ${a.page}` : ''}</span>
            {:else if a.page}<span>On page {a.page}</span>
            {:else}<span>On this {isFile ? 'file' : 'page'}</span>{/if}
          </p>
          {#if isFile && a.x === undefined && !a.page}
            <label class="dd-field"><span>Page</span><input bind:value={pageNo.value} inputmode="numeric" placeholder="e.g. 3 (optional)" /></label>
          {/if}
          <!-- svelte-ignore a11y_autofocus -->
          <textarea id="review-body" bind:value={reviewBody.value} rows="3" placeholder="What should change here?" aria-label="Review comment" autofocus></textarea>
          {#if !isFile && a.quote}
            <label class="dd-field"><span>Suggest replacement text <span class="muted">(optional; Accept applies it)</span></span>
              <textarea bind:value={suggestion.value} rows="2" placeholder={a.quote} aria-label="Suggested replacement"></textarea>
            </label>
          {/if}
          <div class="dd-compose-row">
            <label class="dd-ask"><span class="muted small">Ask</span>
              <select bind:value={reviewAsk.value} aria-label="Ask someone to answer the thread">
                <option value="">no one</option>
                {#each askable as acc (acc.id)}<option value={acc.handle}>{acc.display_name}</option>{/each}
              </select>
            </label>
            <button type="button" class="secondary" onclick={cancelReview}>Cancel</button>
            <button class="primary" disabled={!reviewBody.value.trim() || sending}>Start thread</button>
          </div>
        </form>
      {:else}
        <p class="dd-hint muted small">
          {#if isFile}Click a spot on the image, or <button class="link" onclick={startPageThread}>comment on a page</button>, to start a thread.{:else}Select text on the page and press <b>Comment</b> to start a thread there.{/if}
        </p>
      {/if}
      {#if !shownReview.length && !docs.pendingAnchor}
        <p class="muted pad small">{review.length ? 'Every thread is resolved.' : 'No review threads yet.'}</p>
      {/if}
      {#each shownReview as t (t.root.id)}
        {#if foldedThreads.has(t.root.id) && !unfolded[t.root.id]}
          <button class="dd-folded" id="thread-{t.root.id}" data-thread-folded onclick={() => (unfolded[t.root.id] = true)}>
            <Icon name="history" size={12} />
            <span><b>Outdated</b> · on {revLabel(t.root)} · {anchorLabel(t.root)}</span>
            <span class="muted">{t.replies.length + 1} comment{t.replies.length ? 's' : ''}</span>
          </button>
        {:else}
          {@render thread(t, true)}
        {/if}
      {/each}
      {#if review.length > openReview.length}
        <button class="link small dd-show-resolved" onclick={() => (showResolved = !showResolved)}>{showResolved ? 'Hide resolved' : `Show ${review.length - openReview.length} resolved`}</button>
      {/if}
    </div>
  {/if}
</aside>

{#snippet comment(c: DocComment, root: boolean)}
  <article class="dd-comment" class:mine={c.author_id === me} class:from-agent={c.author_kind === 'agent'} class:outdated={c.outdated} data-comment={c.id}>
    <div class="dd-who">
      <Avatars people={board.accounts[c.author_id] ? [board.accounts[c.author_id]] : []} />
      <strong>{name(c.author_id) ?? 'Someone'}</strong>
      <span class="dd-rev" title={isFile ? `Written on revision ${c.anchor_revision}` : `Written on version ${c.anchor_revision}`} class:stale={c.outdated}>on {revLabel(c)}</span>
      {#if c.assignee_id}<span class="dd-asked" class:open={!c.resolved_at && root}>asked {name(c.assignee_id) ?? 'someone'}</span>{/if}
      <time class="muted" datetime={c.created_at} title={stamp(c.created_at, board.now)}>{ago(c.created_at, board.now)}</time>
      {#if c.edited_at}<span class="muted small">edited</span>{/if}
      {#if c.author_id === me && editing !== c.id}
        <button class="link small dd-edit" onclick={() => { editing = c.id; editText = c.body_md; }}>Edit</button>
      {/if}
    </div>
    {#if editing === c.id}
      <textarea class="dd-edit-box" bind:value={editText} rows="3" aria-label="Edit comment" onkeydown={(e) => { if (isSendKey(e)) { e.preventDefault(); void saveEdit(c); } if (e.key === 'Escape') editing = null; }}></textarea>
      <div class="dd-compose-row"><button class="secondary" onclick={() => (editing = null)}>Cancel</button><button class="primary" onclick={() => void saveEdit(c)}>Save</button></div>
    {:else}
      <div class="md dd-body">{@html html(c)}</div>
    {/if}
    {#if c.suggestion_md}
      <div class="dd-suggestion" class:accepted={!!c.accepted_at}>
        <span class="muted small">{c.accepted_at ? 'Accepted replacement' : 'Suggested replacement'}</span>
        <del>{c.anchor_quote}</del>
        <ins>{c.suggestion_md}</ins>
        {#if !c.accepted_at && !isFile}
          <button class="primary small" onclick={() => void docs.acceptSuggestion(c.id)}><Icon name="check" size={12} /> Accept</button>
        {/if}
      </div>
    {/if}
  </article>
{/snippet}

{#snippet thread(t: Thread, isReview: boolean)}
  <section class="dd-thread" class:resolved={!!t.root.resolved_at} class:review={isReview} id="thread-{t.root.id}" data-thread={t.root.id}>
    {#if isReview}
      <p class="dd-anchor">
        <Icon name="comment" size={13} />
        <span>{anchorLabel(t.root)}</span>
        {#if t.root.resolved_at}<span class="dd-resolved-tag">Resolved</span>{/if}
      </p>
    {/if}
    {@render comment(t.root, true)}
    {#each t.replies as r (r.id)}
      <div class="dd-reply">{@render comment(r, false)}</div>
    {/each}
    <div class="dd-thread-tools">
      {#if replyTo === t.root.id}
        <!-- svelte-ignore a11y_autofocus -->
        <textarea bind:value={replyText.value} rows="2" placeholder="Reply…" aria-label="Reply" autofocus onkeydown={(e) => { if (isSendKey(e)) { e.preventDefault(); void sendReply(t.root); } if (e.key === 'Escape') replyTo = null; }}></textarea>
        <div class="dd-compose-row"><button class="secondary" onclick={() => { replyText.value = ''; replyTo = null; }}>Cancel</button><button class="primary" disabled={!replyText.value.trim() || sending} onclick={() => void sendReply(t.root)}>Reply</button></div>
      {:else}
        <button class="link small" onclick={() => { replyTo = t.root.id; }}><Icon name="reply" size={12} /> Reply</button>
        {#if isReview || t.root.assignee_id}
          {#if t.root.resolved_at}
            <button class="link small" onclick={() => void docs.resolveComment(t.root.id, true)}>Reopen</button>
          {:else}
            <button class="link small" onclick={() => void docs.resolveComment(t.root.id)}><Icon name="check" size={12} /> Resolve</button>
          {/if}
        {/if}
      {/if}
    </div>
  </section>
{/snippet}
