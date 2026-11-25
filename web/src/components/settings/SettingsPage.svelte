<script lang="ts">
  // Workspace Settings (`#/settings`, owners and admins): default filing list, AI task creation
  // provider/model settings, and workspace agent rules.
  import { board } from '../../lib/store.svelte';
  import AgentRules from '../members/AgentRules.svelte';
  import InboxSetting from '../members/InboxSetting.svelte';
  import AiSettings from './AiSettings.svelte';
</script>

<section class="work-page settings-page" aria-label="Workspace settings">
  <header class="settings-head">
    <div>
      <h1>Settings</h1>
      <p class="muted small">
        Workspace defaults, AI task creation, and agent rules.
        {#if board.me?.timezone}
          Timezone: <code>{board.me.timezone}</code>.
        {/if}
      </p>
    </div>
  </header>

  <div class="settings-stack">
    <InboxSetting />
    <AiSettings />
    <AgentRules />
  </div>
</section>

<style>
  .settings-page {
    max-width: 780px;
  }
  .settings-head {
    margin-bottom: 18px;
  }
  .settings-head h1 {
    margin: 0 0 4px;
    font-size: 22px;
  }
  .settings-head p {
    margin: 0;
  }
  .settings-stack {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
</style>
