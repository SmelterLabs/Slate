<script lang="ts">
  // The board's shape while the first snapshot loads: a header, a tool row and a few columns
  // of shimmering cards. Pure decoration (aria-hidden); the status line says what is happening.
  const cols = [3, 2, 4, 1];
</script>

<div class="skeleton-page" role="status" aria-live="polite">
  <span class="sr-only">Loading…</span>
  <div class="sk-row" aria-hidden="true">
    <span class="sk" style="width: 180px; height: 22px"></span>
  </div>
  <div class="sk-row" aria-hidden="true">
    {#each [64, 52, 58, 72] as w, i (i)}<span class="sk" style="width: {w}px; height: 26px"></span>{/each}
    <span style="flex: 1"></span>
    <span class="sk" style="width: 200px; height: 26px"></span>
  </div>
  <div class="skeleton-cols" aria-hidden="true">
    {#each cols as n, c (c)}
      <div class="skeleton-col">
        <span class="sk" style="width: {60 + c * 14}px; height: 14px; margin: 2px 0 4px"></span>
        {#each Array(n) as _, i (i)}
          <div class="skeleton-card">
            <span class="sk" style="width: {88 - ((i + c) % 3) * 18}%; height: 12px"></span>
            <span class="sk" style="width: 45%; height: 10px"></span>
          </div>
        {/each}
      </div>
    {/each}
  </div>
</div>
