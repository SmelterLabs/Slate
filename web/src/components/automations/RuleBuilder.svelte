<script lang="ts">
  // The rule builder: a centered window reading top to bottom like ClickUp's — name and scope,
  // WHEN (one trigger), IF (any number of conditions, all must hold), THEN (one or more
  // actions) — with the rule in plain words underneath as it is filled in. Save is one POST or
  // PATCH; the server validates and its error is shown here.
  import {
    automations,
    summarize,
    type CatalogEntry,
    type Condition,
    type RuleDraft,
    type Step,
  } from '../../lib/automations.svelte';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';
  import ParamInput from './ParamInput.svelte';

  let {
    draft = $bindable(),
    id = null,
    onclose,
  }: { draft: RuleDraft; id?: string | null; onclose: () => void } = $props();

  let saving = $state(false);
  let error = $state<string | null>(null);

  const catalog = $derived(automations.catalog);
  const trigger = $derived(catalog?.triggers.find((t) => t.type === draft.trigger.type));
  const scope = $derived(draft.list_id ? `list:${draft.list_id}` : draft.section_id ? `section:${draft.section_id}` : '');
  const listName = (lid: string) => board.lists[lid]?.name ?? 'a list';
  const words = $derived(summarize(draft, catalog, listName));

  function setScope(v: string) {
    const [kind, sid] = v.split(':');
    draft.list_id = kind === 'list' ? sid : null;
    draft.section_id = kind === 'section' ? sid : null;
  }

  function pick(entry: CatalogEntry | undefined, old: Step): Step {
    const next: Step = { type: entry?.type ?? old.type };
    for (const p of entry?.params ?? []) next[p.name] = p.kind === 'number' ? 0 : '';
    return next;
  }

  function setTrigger(type: string) {
    draft.trigger = pick(
      catalog?.triggers.find((t) => t.type === type),
      draft.trigger,
    );
  }

  function setAction(i: number, type: string) {
    draft.actions[i] = pick(
      catalog?.actions.find((a) => a.type === type),
      draft.actions[i],
    );
  }

  function addCondition() {
    draft.conditions.push({ field: 'priority', op: 'is', value: '' } satisfies Condition);
  }

  function addAction() {
    draft.actions.push(pick(catalog?.actions.find((a) => a.type === 'add_tag'), { type: 'add_tag' }));
  }

  async function save(e: SubmitEvent) {
    e.preventDefault();
    saving = true;
    error = null;
    try {
      await automations.save(draft, id ?? undefined);
      board.flash(id ? 'Automation saved' : 'Automation created');
      onclose();
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  const statusKinds = ['open', 'active', 'done', 'cancelled'];
</script>

<button class="drawer-scrim" aria-label="Close" tabindex="-1" onclick={onclose}></button>
<div class="builder" role="dialog" aria-modal="true" aria-label={id ? 'Edit automation' : 'New automation'}>
  <form onsubmit={save}>
    <header>
      <span class="bolt" aria-hidden="true"><Icon name="sparkle" size={18} /></span>
      <input
        class="name"
        bind:value={draft.name}
        placeholder="Name this automation"
        aria-label="Automation name"
        maxlength="120"
      />
      <button type="button" class="icon" aria-label="Close" onclick={onclose}><Icon name="x" size={18} /></button>
    </header>

    <div class="body">
      <label class="scope">
        <span>Runs in</span>
        <select value={scope} onchange={(e) => setScope(e.currentTarget.value)} aria-label="Scope">
          <option value="">Every list</option>
          {#each board.sections as sec (sec.id)}
            <option value="section:{sec.id}">{sec.name} (whole section)</option>
            {#each board.listsOf(sec.id) as l (l.id)}<option value="list:{l.id}">&nbsp;&nbsp;{sec.name} › {l.name}</option>{/each}
          {/each}
        </select>
      </label>

      <section class="block when">
        <h3><span class="pill">When</span> this happens</h3>
        <div class="rb-row">
          <select
            value={draft.trigger.type}
            onchange={(e) => setTrigger(e.currentTarget.value)}
            aria-label="Trigger"
          >
            {#each catalog?.triggers ?? [] as t (t.type)}<option value={t.type}>{t.label}</option>{/each}
          </select>
          {#each trigger?.params ?? [] as p (p.name)}
            <label class="rb-param">
              <span>{p.name.replace('_', ' ')}</span>
              <ParamInput param={p} bind:value={draft.trigger[p.name]} listId={draft.list_id} id="trigger-{p.name}" />
            </label>
          {/each}
        </div>
      </section>

      <section class="block if">
        <h3><span class="pill">If</span> all of these hold <span class="muted">(optional)</span></h3>
        {#each draft.conditions as c, i (i)}
          <div class="rb-row cond">
            <select bind:value={c.field} onchange={() => (c.value = '')} aria-label="Condition field">
              {#each catalog?.conditions.fields ?? [] as f (f.field)}<option value={f.field}>{f.label}</option>{/each}
            </select>
            <select bind:value={c.op} aria-label="Condition">
              <option value="is">is</option>
              <option value="is_not">is not</option>
            </select>
            {#if c.field === 'status_kind'}
              <select bind:value={c.value} aria-label="Value">
                <option value="" disabled>choose</option>
                {#each statusKinds as k (k)}<option value={k}>{k}</option>{/each}
              </select>
            {:else if c.field === 'assignee'}
              <select bind:value={c.value} aria-label="Value">
                <option value="" disabled>choose</option>
                <option value="nobody">nobody</option>
                {#each Object.values(board.accounts).filter((a) => a.handle !== 'slate') as a (a.id)}
                  <option value={a.handle}>{a.display_name}</option>
                {/each}
              </select>
            {:else}
              <ParamInput
                param={{
                  name: 'value',
                  kind: c.field === 'tag' ? 'tag' : c.field === 'priority' ? 'priority' : c.field === 'list' ? 'list' : 'status',
                  required: true,
                  help: 'value',
                }}
                bind:value={c.value as string | number | undefined}
                listId={draft.list_id}
                id="cond-{i}"
              />
            {/if}
            <button type="button" class="icon" aria-label="Remove condition" onclick={() => draft.conditions.splice(i, 1)}
              ><Icon name="x" size={14} /></button
            >
          </div>
        {/each}
        <button type="button" class="link add" onclick={addCondition}><Icon name="plus" size={14} /> Add condition</button>
      </section>

      <section class="block then">
        <h3><span class="pill">Then</span> do this</h3>
        {#each draft.actions as a, i (i)}
          {@const spec = catalog?.actions.find((x) => x.type === a.type)}
          <div class="rb-row action">
            <span class="n">{i + 1}</span>
            <select value={a.type} onchange={(e) => setAction(i, e.currentTarget.value)} aria-label="Action {i + 1}">
              {#each catalog?.actions ?? [] as x (x.type)}<option value={x.type}>{x.label}</option>{/each}
            </select>
            {#each spec?.params ?? [] as p (p.name)}
              <span class="rb-param grow" class:wide={p.kind === 'text'}>
                <ParamInput param={p} bind:value={a[p.name]} listId={draft.list_id} id="action-{i}-{p.name}" />
              </span>
            {/each}
            {#if draft.actions.length > 1}
              <button type="button" class="icon" aria-label="Remove action {i + 1}" onclick={() => draft.actions.splice(i, 1)}
                ><Icon name="x" size={14} /></button
              >
            {/if}
          </div>
        {/each}
        <button type="button" class="link add" onclick={addAction}><Icon name="plus" size={14} /> Add action</button>
      </section>

      <p class="words" aria-live="polite">{words}</p>
      {#if error}<p class="error" role="alert">{error}</p>{/if}
    </div>

    <footer>
      <label class="on"><input type="checkbox" bind:checked={draft.enabled} /> On</label>
      <span class="grow"></span>
      <button type="button" class="secondary" onclick={onclose}>Cancel</button>
      <button class="primary" disabled={saving || !catalog}>{saving ? 'Saving…' : id ? 'Save' : 'Create automation'}</button>
    </footer>
  </form>
</div>

<style>
  .builder {
    position: fixed;
    z-index: 60;
    left: 50%;
    top: 6vh;
    translate: -50% 0;
    width: min(760px, calc(100vw - 24px));
    max-height: 88vh;
    display: flex;
    flex-direction: column;
    background: var(--raised);
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-modal);
    animation: fade var(--dur-2) var(--ease);
  }
  @keyframes fade {
    from {
      opacity: 0;
    }
  }
  form {
    display: flex;
    flex-direction: column;
    min-height: 0;
    flex: 1;
  }
  header,
  footer {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 16px;
  }
  header {
    border-bottom: 1px solid var(--line);
  }
  footer {
    border-top: 1px solid var(--line);
  }
  .bolt {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: var(--brand-gradient);
    color: #fff;
    flex: none;
  }
  .name {
    flex: 1;
    font-size: var(--fs-lg);
    font-weight: 600;
    background: transparent;
    border: 0;
    color: var(--text);
  }
  .body {
    overflow: auto;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .scope {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
  }
  .block {
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .block h3 {
    margin: 0;
    font-size: var(--fs-sm);
    font-weight: 600;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .pill {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 8px;
    border-radius: var(--radius-pill);
    color: #fff;
  }
  .when .pill {
    background: var(--accent);
  }
  .if .pill {
    background: var(--warning);
  }
  .then .pill {
    background: var(--success);
  }
  .rb-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .rb-param {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
    font-size: var(--fs-sm);
  }
  .rb-param.wide {
    flex-basis: 100%;
  }
  .n {
    width: 20px;
    height: 20px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: var(--accent-soft);
    color: var(--accent-ink);
    font-size: 11px;
    font-weight: 600;
  }
  .add {
    align-self: flex-start;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .words {
    margin: 0;
    padding: 10px 12px;
    border-radius: var(--radius);
    background: var(--accent-soft);
    color: var(--accent-ink);
  }
  .error {
    margin: 0;
    color: var(--danger);
  }
  .grow {
    flex: 1;
  }
  .on {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  @media (max-width: 760px) {
    .builder {
      top: 0;
      left: 0;
      translate: none;
      width: 100vw;
      max-height: none;
      height: 100dvh;
      border-radius: 0;
      padding: env(safe-area-inset-top) 0 env(safe-area-inset-bottom);
    }
  }
</style>
