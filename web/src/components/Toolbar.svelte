<script lang="ts">
  // ClickUp's view bar: layout and Group on the left; Sort, Filter, Closed, Me, Search,
  // Customize and "+ Task" on the right; then a chip per active filter rule. Every control edits
  // the view's draft (board.setConfig); the view tabs offer Save and Revert.
  import { describeRule } from '../lib/filterfields';
  import { board } from '../lib/store.svelte';
  import type { Theme } from '../lib/theme';
  import {
    ALL_CARD_FIELDS,
    CARD_FIELD_NAMES,
    GROUP_NAMES,
    SORT_NAMES,
    isActiveFilter,
    isGroup,
    keyName,
    ruleCount,
    type CardField,
    type FilterRule,
    type GroupBy,
    type SortField,
    type SortKey,
  } from '../lib/views';
  import Dropdown from './Dropdown.svelte';
  import FilterBuilder from './FilterBuilder.svelte';
  import Icon from './Icon.svelte';

  const config = $derived(board.active.config);
  const scope = $derived(board.selection);
  const groupOptions = $derived(
    (Object.keys(GROUP_NAMES) as GroupBy[]).filter(
      (g) =>
        (g !== 'status' || scope.kind === 'list') &&
        (g !== 'list' || scope.kind !== 'list') &&
        (g !== 'section' || scope.kind === 'everything'),
    ),
  );
  const sortOptions = Object.keys(SORT_NAMES) as SortField[];
  const cardOptions = ALL_CARD_FIELDS as CardField[];
  const rules = $derived(ruleCount(config.filter));
  // Keep original indices so removing a visible chip never removes a hidden draft.
  const activeRules = $derived(config.filter.rules.map((rule, i) => ({ rule, i })).filter(({ rule }) => isActiveFilter(rule)));
  let filterOpen = $state(false);
  let groupOpen = $state(false);
  const theme = $derived(board.theme);

  const cardName = (f: CardField) => CARD_FIELD_NAMES[f];
  // "Me": a top-level "assignee is me" rule, toggled from one button as in ClickUp.
  const isMeRule = (r: FilterRule | (typeof config.filter.rules)[number]) =>
    !isGroup(r) && r.field === 'assignee' && r.cmp === 'is' && JSON.stringify(r.value) === '["me"]';
  const meOn = $derived(config.filter.rules.some(isMeRule));
  function toggleMe() {
    const rest = config.filter.rules.filter((r) => !isMeRule(r));
    board.setConfig({
      filter: {
        ...config.filter,
        op: meOn ? config.filter.op : 'and',
        rules: meOn ? rest : [...rest, { field: 'assignee', cmp: 'is', value: ['me'] }],
      },
    });
  }

  function setSort(i: number, key: SortKey | null) {
    const next = [...config.sort];
    if (key) next[i] = key;
    else next.splice(i, 1);
    board.setConfig({ sort: next });
  }
  function toggleField(f: CardField, on: boolean) {
    const set = new Set(config.card_fields);
    if (on) set.add(f);
    else set.delete(f);
    board.setConfig({ card_fields: cardOptions.filter((x) => set.has(x)) });
  }
  function pickTheme(t: Theme) {
    board.setTheme(t);
  }
  const sortLabel = $derived(
    config.sort.length && config.sort[0].field !== 'manual'
      ? `${keyName(config.sort[0].field)}${config.sort.length > 1 ? ` +${config.sort.length - 1}` : ''}`
      : 'Sort',
  );
</script>

<div class="toolbar cu-bar" role="toolbar" aria-label="View options">
  <div class="seg" role="group" aria-label="Layout">
    <button class:on={config.layout === 'board'} onclick={() => board.setConfig({ layout: 'board' })} title="Board (b)"
      >Board</button
    >
    <button class:on={config.layout === 'list'} onclick={() => board.setConfig({ layout: 'list' })} title="List (b)"
      >List</button
    >
  </div>

  <Dropdown title="Group by" cls="chip-btn {config.group_by !== 'none' ? 'active' : ''}" bind:open={groupOpen}>
    {#snippet label()}<Icon name="layers" /> Group: {keyName(config.group_by)}{/snippet}
    <div class="menu" role="menu" aria-label="Group options">
      {#each groupOptions as g (g)}
        <button
          role="menuitemradio"
          aria-checked={config.group_by === g}
          class:on={config.group_by === g}
          onclick={() => {
            groupOpen = false;
            board.setConfig({ group_by: g });
          }}>{keyName(g)}{#if config.group_by === g}<Icon name="check" />{/if}</button
        >
      {/each}
    </div>
  </Dropdown>
  <button
    class="chip-btn"
    class:active={config.show_subtasks}
    aria-pressed={config.show_subtasks}
    title="Show subtasks as their own cards"
    onclick={() => board.setConfig({ show_subtasks: !config.show_subtasks })}
    ><Icon name="subtasks" /> Subtasks</button
  >

  <span class="tb-spacer"></span>

  <Dropdown title="Sort" cls="chip-btn {config.sort.length && config.sort[0].field !== 'manual' ? 'active' : ''}" align="right">
    {#snippet label()}<Icon name="sort" /> {sortLabel}{/snippet}
    <div class="tb-panel">
      {#each config.sort as key, i (i)}
        <div class="inline">
          <span class="muted small">{i === 0 ? 'Sort by' : 'then'}</span>
          <select
            aria-label="Sort field {i + 1}"
            value={key.field}
            onchange={(e) => setSort(i, { ...key, field: e.currentTarget.value as SortField })}
          >
            {#each sortOptions as f (f)}<option value={f}>{keyName(f)}</option>{/each}
          </select>
          <button class="chip" onclick={() => setSort(i, { ...key, dir: key.dir === 'asc' ? 'desc' : 'asc' })}
            >{key.dir === 'asc' ? '↑ asc' : '↓ desc'}</button
          >
          <button class="icon small" aria-label="Remove sort" onclick={() => setSort(i, null)}><Icon name="x" size={12} /></button>
        </div>
      {/each}
      <button
        class="link small"
        onclick={() => board.setConfig({ sort: [...config.sort, { field: 'due', dir: 'asc' }] })}>+ Sort key</button
      >
      <p class="muted small">Empty values always sort last.</p>
    </div>
  </Dropdown>

  <Dropdown title="Filter" id="filter-btn" bind:open={filterOpen} cls="chip-btn {rules ? 'active' : ''}" align="right">
    {#snippet label()}<Icon name="filter" /> Filter{#if rules}&nbsp;<span class="pill">{rules}</span>{/if}{/snippet}
    <div class="tb-panel wide">
      <FilterBuilder node={config.filter} onchange={(n) => board.setConfig({ filter: n })} />
    </div>
  </Dropdown>

  <button
    class="chip-btn"
    class:active={config.show_closed}
    aria-pressed={config.show_closed}
    title="Show tasks closed on earlier days"
    onclick={() => board.setConfig({ show_closed: !config.show_closed })}><Icon name="closed" /> Closed</button
  >
  <button class="chip-btn" class:active={meOn} aria-pressed={meOn} title="Only tasks assigned to me" onclick={toggleMe}
    ><Icon name="me" /> Me</button
  >

  <label class="search-box">
    <Icon name="search" />
    <input id="view-search" class="search" type="search" placeholder="Search  /" aria-label="Search" bind:value={board.q} />
  </label>

  <!-- float: the panel is taller than most windows; anchored inside the page it stretched the
       scroll height, and closing it made the content jump out from under the next click. -->
  <Dropdown float title="Customize" cls="chip-btn" align="right">
    {#snippet label()}<Icon name="gear" /> Customize{/snippet}
    <div class="tb-panel customize" aria-label="Customize view">
      <div class="cz-head">Show on cards</div>
      <div class="cz-fields">
        {#each cardOptions as f (f)}
          <label class="switch-row"
            ><span>{cardName(f)}</span><input
              type="checkbox"
              role="switch"
              checked={config.card_fields.includes(f)}
              onchange={(e) => toggleField(f, e.currentTarget.checked)}
            /></label
          >
        {/each}
      </div>
      <div class="cz-head">Task name</div>
      <div class="seg" role="group" aria-label="Title lines">
        {#each [1, 2, 3, 0] as n (n)}
          <button class:on={config.title_lines === n} onclick={() => board.setConfig({ title_lines: n })}
            >{n ? `${n} line${n > 1 ? 's' : ''}` : 'Full'}</button
          >
        {/each}
      </div>
      <div class="cz-head">Cards</div>
      <div class="seg" role="group" aria-label="Density">
        <button class:on={config.density === 'comfortable'} onclick={() => board.setConfig({ density: 'comfortable' })}
          >Comfortable</button
        >
        <button class:on={config.density === 'compact'} onclick={() => board.setConfig({ density: 'compact' })}
          >Compact</button
        >
      </div>
      <div class="cz-head">Columns</div>
      <label class="switch-row"
        ><span>Fold empty columns</span><input
          type="checkbox"
          role="switch"
          checked={config.collapse_empty}
          onchange={(e) => board.setConfig({ collapse_empty: e.currentTarget.checked })}
        /></label
      >
      <label class="switch-row"
        ><span>Hide empty columns</span><input
          type="checkbox"
          role="switch"
          checked={config.hide_empty_groups}
          onchange={(e) => board.setConfig({ hide_empty_groups: e.currentTarget.checked })}
        /></label
      >
      <label class="switch-row"
        ><span>Closed items (done earlier days)</span><input
          type="checkbox"
          role="switch"
          checked={config.show_closed}
          onchange={(e) => board.setConfig({ show_closed: e.currentTarget.checked })}
        /></label
      >
      <label class="switch-row"
        ><span>Subtasks as their own cards</span><input
          type="checkbox"
          role="switch"
          checked={config.show_subtasks}
          onchange={(e) => board.setConfig({ show_subtasks: e.currentTarget.checked })}
        /></label
      >
      {#if config.group_by === 'due'}
        <label class="switch-row"
          ><span>Named days after today</span><input
            class="tiny"
            type="number"
            min="0"
            max="30"
            aria-label="Due window days"
            value={config.due_window_days}
            onchange={(e) =>
              board.setConfig({ due_window_days: Math.max(0, Math.min(30, Number(e.currentTarget.value) || 0)) })}
          /></label
        >
      {/if}
      <div class="cz-head">Theme <span class="muted small">(this browser)</span></div>
      <div class="seg" role="group" aria-label="Theme">
        {#each ['auto', 'light', 'dark'] as const as t (t)}
          <button class:on={theme === t} onclick={() => pickTheme(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
        {/each}
      </div>
    </div>
  </Dropdown>

  <button
    class="primary add-task"
    title="New task (c)"
    onclick={() => document.getElementById('quick-add')?.focus()}><Icon name="plus" /> Task</button
  >
</div>

{#if rules}
  <div class="chips" aria-label="Active filters">
    {#each activeRules as { rule, i }, visibleIndex (i)}
      <span class="chip filter-chip">
        <button class="plain" onclick={() => (filterOpen = true)}>{describeRule(rule, board.ctx)}</button>
        <button
          class="x"
          aria-label="Remove filter"
          onclick={() => board.setConfig({ filter: { ...config.filter, rules: config.filter.rules.filter((_, j) => j !== i) } })}
          ><Icon name="x" size={11} /></button
        >
      </span>
      {#if visibleIndex < activeRules.length - 1}<span class="muted small">{config.filter.op}</span>{/if}
    {/each}
    <button class="link small" onclick={() => board.setConfig({ filter: { op: 'and', rules: [] } })}>Clear filters</button>
  </div>
{/if}
