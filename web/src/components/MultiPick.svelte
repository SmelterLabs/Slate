<script lang="ts">
  // Checkbox list for filter values (statuses, tags, people…); a search box once it is long.
  let {
    options,
    value,
    onchange,
    label,
  }: {
    options: { value: string; label: string }[];
    value: string[];
    onchange: (v: string[]) => void;
    label: string;
  } = $props();

  let q = $state('');
  const shown = $derived(
    q.trim() ? options.filter((o) => o.label.toLowerCase().includes(q.trim().toLowerCase())) : options,
  );

  function toggle(v: string, on: boolean) {
    onchange(on ? [...value, v] : value.filter((x) => x !== v));
  }
</script>

<div class="multipick" role="group" aria-label={label}>
  {#if options.length > 8}
    <input type="search" placeholder="Find…" aria-label="Find {label}" bind:value={q} />
  {/if}
  <div class="mp-options">
    {#each shown as o (o.value)}
      <label class="mp-opt">
        <input
          type="checkbox"
          checked={value.includes(o.value)}
          onchange={(e) => toggle(o.value, e.currentTarget.checked)}
        />
        <span>{o.label}</span>
      </label>
    {:else}
      <span class="muted small">None</span>
    {/each}
  </div>
</div>
