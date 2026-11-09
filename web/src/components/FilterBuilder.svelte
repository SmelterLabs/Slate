<script lang="ts">
  // Rule builder for a view's filter: AND/OR groups nested up to three levels (the server's cap).
  // Works on a copy and hands back the whole new group, so a draft only changes through
  // board.setConfig.
  import { accountFilterOptions, fieldDef, fieldsFor, initialValue, newRule } from '../lib/filterfields';
  import { priorityLong } from '../lib/priority';
  import { board } from '../lib/store.svelte';
  import type { FilterGroup, FilterRule, Scope } from '../lib/views';
  import type { Priority } from '../lib/types';
  import { KINDS, KIND_NAMES, PRIORITIES, isActiveRule, isGroup } from '../lib/views';
  import MultiPick from './MultiPick.svelte';
  import Self from './FilterBuilder.svelte';
  import Icon from './Icon.svelte';

  let {
    node,
    onchange,
    depth = 1,
    onremove = undefined,
    scope: scopeProp = undefined,
  }: {
    node: FilterGroup;
    onchange: (n: FilterGroup) => void;
    depth?: number;
    onremove?: () => void;
    /** The scope whose fields and statuses to offer; the board's selection by default. */
    scope?: Scope;
  } = $props();

  const scope = $derived(scopeProp ?? board.selection);
  const fields = $derived(fieldsFor(scope));

  const options = $derived.by(() => {
    const list = scope.kind === 'list' ? board.lists[scope.id] : undefined;
    const tags = new Set<string>();
    for (const i of board.plainItems) for (const t of i.tags) tags.add(t);
    return {
      statuses: (list?.statuses ?? []).map((s) => ({ value: s.id, label: s.name })),
      kinds: KINDS.map((k) => ({ value: k, label: KIND_NAMES[k] })),
      tags: [...tags].sort().map((t) => ({ value: t, label: `#${t}` })),
      priorities: [...PRIORITIES, 'none'].map((p) => ({ value: p, label: p === 'none' ? 'No priority' : priorityLong(p as Priority) })),
      lists: board.sections.flatMap((s) =>
        board
          .listsOf(s.id)
          .filter((l) => scope.kind !== 'section' || l.section_id === scope.id)
          .map((l) => ({ value: l.id, label: `${s.name} · ${l.name}` })),
      ),
      sections: board.sections.map((s) => ({ value: s.id, label: s.name })),
    };
  });

  function set(i: number, rule: FilterRule | FilterGroup) {
    onchange({ ...node, rules: node.rules.map((r, j) => (j === i ? rule : r)) });
  }
  function remove(i: number) {
    onchange({ ...node, rules: node.rules.filter((_, j) => j !== i) });
  }
  function setPicked(i: number, rule: FilterRule, value: string[]) {
    // Explicitly clearing a selection removes it; freshly added [] drafts remain editable.
    if (!value.length) remove(i);
    else set(i, { ...rule, value });
  }
  function setField(i: number, field: string) {
    set(i, newRule(field));
  }
  function setCmp(i: number, rule: FilterRule, cmp: string) {
    const def = fieldDef(rule.field);
    const keep = def && def.value(cmp) === def.value(rule.cmp);
    set(i, { ...rule, cmp, value: keep ? rule.value : initialValue(def?.value(cmp) ?? 'none') });
  }
  const arr = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);
  const pair = (v: unknown): [string, string] => {
    const a = Array.isArray(v) ? v : [];
    return [String(a[0] ?? ''), String(a[1] ?? '')];
  };
</script>

<div class="fb-group" class:nested={depth > 1}>
  <div class="fb-head">
    {#if node.rules.length > 1}
      <label class="small"
        >Match
        <select
          aria-label="Match"
          value={node.op}
          onchange={(e) => onchange({ ...node, op: e.currentTarget.value as 'and' | 'or' })}
        >
          <option value="and">all of these</option>
          <option value="or">any of these</option>
        </select>
      </label>
    {:else if !node.rules.length}
      <span class="muted small">No rules{depth === 1 ? ': showing everything in scope' : ''}.</span>
    {/if}
    {#if onremove}<button class="link danger small" onclick={onremove}>Remove group</button>{/if}
  </div>

  {#each node.rules as rule, i (i)}
    {#if isGroup(rule)}
      <Self node={rule} {scope} depth={depth + 1} onchange={(n) => set(i, n)} onremove={() => remove(i)} />
    {:else}
      {@const def = fieldDef(rule.field)}
      {@const kind = def?.value(rule.cmp) ?? 'none'}
      <div class="fb-rule" data-field={rule.field}>
        <select aria-label="Field" value={rule.field} onchange={(e) => setField(i, e.currentTarget.value)}>
          {#each fields as f (f.field)}<option value={f.field}>{f.label}</option>{/each}
        </select>
        <select aria-label="Comparison" value={rule.cmp} onchange={(e) => setCmp(i, rule, e.currentTarget.value)}>
          {#each def?.cmps ?? [] as c (c.cmp)}<option value={c.cmp}>{c.label}</option>{/each}
        </select>
        <button class="icon small" aria-label="Remove rule" onclick={() => remove(i)}><Icon name="x" size={12} /></button>
        {#if !isActiveRule(rule)}<span class="muted small">Incomplete — choose a value</span>{/if}
        <div class="fb-value">
          {#if kind === 'statuses' || kind === 'kinds' || kind === 'people' || kind === 'tags' || kind === 'priorities' || kind === 'lists' || kind === 'sections'}
            <MultiPick
              label={def?.label ?? rule.field}
              options={kind === 'people' ? accountFilterOptions(board.pickable, board.accounts, rule.value) : options[kind]}
              value={arr(rule.value)}
              onchange={(v) => setPicked(i, rule, v)}
            />
          {:else if kind === 'number'}
            <input
              class="tiny"
              type="number"
              aria-label="Number"
              value={rule.value == null ? '' : Number(rule.value)}
              oninput={(e) => set(i, { ...rule, value: e.currentTarget.value === '' ? null : Number(e.currentTarget.value) })}
            />
          {:else if kind === 'person'}
            <select
              aria-label="Who"
              value={String(rule.value ?? 'me')}
              onchange={(e) => set(i, { ...rule, value: e.currentTarget.value })}
            >
              {#each accountFilterOptions(board.pickable, board.accounts, rule.value) as p (p.value)}<option value={p.value}>{p.label}</option>{/each}
            </select>
          {:else if kind === 'text'}
            <input
              aria-label="Text"
              value={String(rule.value ?? '')}
              oninput={(e) => set(i, { ...rule, value: e.currentTarget.value })}
            />
          {:else if kind === 'bool'}
            <select
              aria-label="Yes or no"
              value={String(rule.value === true || rule.value === 'true')}
              onchange={(e) => set(i, { ...rule, value: e.currentTarget.value === 'true' })}
            >
              <option value="true">yes</option>
              <option value="false">no</option>
            </select>
          {:else if kind === 'days'}
            <input
              class="tiny"
              type="number"
              min="0"
              max="365"
              aria-label="Days"
              value={rule.value == null ? '' : Number(rule.value)}
              oninput={(e) => set(i, { ...rule, value: e.currentTarget.value === '' ? null : Number(e.currentTarget.value) })}
            /> days
          {:else if kind === 'date'}
            <input
              type="date"
              aria-label="Date"
              value={String(rule.value ?? '')}
              onchange={(e) => set(i, { ...rule, value: e.currentTarget.value })}
            />
          {:else if kind === 'dates'}
            {@const [a, b] = pair(rule.value)}
            <input type="date" aria-label="From" value={a} onchange={(e) => set(i, { ...rule, value: [e.currentTarget.value, b] })} />
            and
            <input type="date" aria-label="To" value={b} onchange={(e) => set(i, { ...rule, value: [a, e.currentTarget.value] })} />
          {/if}
        </div>
      </div>
    {/if}
  {/each}

  <div class="fb-add">
    <button class="link small" onclick={() => onchange({ ...node, rules: [...node.rules, newRule()] })}>+ Rule</button>
    {#if depth < 3}
      <button
        class="link small"
        onclick={() =>
          onchange({ ...node, rules: [...node.rules, { op: node.op === 'and' ? 'or' : 'and', rules: [newRule()] }] })}
        >+ Group</button
      >
    {/if}
  </div>
</div>
