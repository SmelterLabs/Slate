<script lang="ts">
  // "Agents may not…": the workspace's agent deny-list (service/agent_rules.py), on the Members
  // page. Each box saves on change; a change by anyone reloads it live (`settings.changed`).
  // It limits agent accounts whatever their token; people, owners and admins included, never.
  import { onDestroy } from 'svelte';
  import { get, put } from '../../lib/api';
  import { changesAgentRules, toggleDenied, type AgentRules } from '../../lib/permissions';
  import { board } from '../../lib/store.svelte';

  let rules = $state<AgentRules | null>(null);
  let busy = $state(false);

  async function load() {
    try {
      rules = await get<AgentRules>('/settings/agents');
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }
  $effect(() => {
    void load();
  });
  const off = board.onEvent((ev) => {
    if (changesAgentRules(ev)) void load();
  });
  onDestroy(off);

  async function toggle(key: string, deny: boolean) {
    if (!rules) return;
    busy = true;
    try {
      rules = await put<AgentRules>('/settings/agents', { denied: toggleDenied(rules, key, deny) });
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
      await load();
    } finally {
      busy = false;
    }
  }
</script>

{#if rules}
  <section class="agent-rules" aria-label="Agents may not">
    <div>
      <strong>Agents may not…</strong>
      <span class="muted small">
        Whatever their token or role. People are never limited by this.{#if rules.default}&nbsp;These are the defaults.{/if}
      </span>
    </div>
    <ul>
      {#each rules.actions as a (a.key)}
        <li>
          <label>
            <input
              type="checkbox"
              checked={rules.denied.includes(a.key)}
              disabled={busy}
              data-agent-rule={a.key}
              onchange={(e) => toggle(a.key, e.currentTarget.checked)}
            />
            {a.label}
          </label>
        </li>
      {/each}
    </ul>
  </section>
{/if}
